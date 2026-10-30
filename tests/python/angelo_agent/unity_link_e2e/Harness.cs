// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Protocol harness for Angelo Link's engine-free half (Editor/LinkProtocol.cs, LinkClient.cs, MiniJson.cs), compiled
// and run outside Unity, never shipped in the package. run.py beside it builds
// and runs it; see doc/angelo/unity_link.rst, "Validation".
//
//   vector <token hex> <server nonce> <client nonce>   print the client and server proofs (compared with Angelo's)
//   offline                                            fake servers: a wrong proof, a good one, split frames
//   e2e <project> <asset path> <seconds> [engine]      the Unity side of a real Angelo session, as AngeloLink.cs
//                                                      drives it: open, scenes, saved -> "reimported", close.
//                                                      engine (default "unity") is the auth frame's client.engine
// Prints one JSON report line and exits 0 when every check passed.

using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using Hyperbond.AngeloLink;

static class Harness
{
    static readonly Dictionary<string, object> Checks = new Dictionary<string, object>();

    static int Main(string[] args)
    {
        try
        {
            switch (args.Length > 0 ? args[0] : "")
            {
                case "vector":
                    var token = LinkProtocol.FromHex(args[1]);
                    Console.WriteLine(MiniJson.Write(new Dictionary<string, object>
                    {
                        ["protocol"] = LinkProtocol.Protocol,
                        ["client"] = LinkProtocol.ClientProof(token, args[2], args[3]),
                        ["server"] = LinkProtocol.ServerProof(token, args[2], args[3]),
                    }));
                    return 0;
                case "offline": Offline(); break;
                case "e2e": EndToEnd(args[1], args[2], double.Parse(args[3]), args.Length > 4 ? args[4] : LinkProtocol.Engine); break;
                default: Console.Error.WriteLine("usage: vector | offline | e2e"); return 2;
            }
        }
        catch (Exception error)
        {
            Checks["exception"] = error.ToString();
        }
        var ok = !Checks.ContainsKey("exception");
        foreach (var value in Checks.Values)
            if (value is bool flag && !flag) ok = false;
        Checks["ok"] = ok;
        Console.WriteLine(MiniJson.Write(Checks));
        return ok ? 0 : 1;
    }

    // Fake servers -----------------------------------------------------------------------------------------------

    static void Offline()
    {
        var json = MiniJson.ParseObject("{\"a\":[1,2.5,\"x\\u00e9\\n\",true,null],\"b\":{}}");
        Checks["json_roundtrip"] = MiniJson.Write(json) == "{\"a\":[1,2.5,\"xé\\n\",true,null],\"b\":{}}";
        Checks["wrong_server_proof_rejected"] = FakeSession(goodProof: false, out var wrong) == LinkState.Off && wrong == 0;
        Checks["good_server_accepted_and_split_frame_read"] = FakeSession(goodProof: true, out var messages)
            == LinkState.Ready && messages == 1;
    }

    /// <summary>A one-connection server that runs the handshake (with a correct or a wrong server proof), then sends
    /// a scene frame in two pieces. Returns the client's final state; <paramref name="messages"/> counts what the
    /// client passed on.</summary>
    static LinkState FakeSession(bool goodProof, out int messages)
    {
        var token = new byte[32];
        new Random(7).NextBytes(token);
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        var record = Path.Combine(Path.GetTempPath(), "angelo-link-harness-" + Guid.NewGuid().ToString("N") + ".json");
        File.WriteAllText(record, MiniJson.Write(new Dictionary<string, object>
        {
            ["protocol"] = LinkProtocol.Protocol, ["port"] = port, ["pid"] = 1, ["token"] = LinkProtocol.ToHex(token),
        }));
        var server = new Thread(() =>
        {
            using (var socket = listener.AcceptSocket())
            {
                var serverNonce = LinkProtocol.NewNonce();
                socket.Send(LinkProtocol.Encode(new Dictionary<string, object>
                    { ["type"] = "hello", ["protocol"] = LinkProtocol.Protocol, ["nonce"] = serverNonce }));
                var reader = new FrameReader();
                var buffer = new byte[4096];
                Dictionary<string, object> auth;
                while (!reader.TryTake(out auth, out _))
                    reader.Feed(buffer, socket.Receive(buffer));
                var clientNonce = LinkProtocol.Text(auth, "nonce");
                var proofOk = LinkProtocol.ProofMatches(LinkProtocol.ClientProof(token, serverNonce, clientNonce),
                                                        auth["proof"]);
                Checks[goodProof ? "server_saw_valid_client_proof" : "server_saw_valid_client_proof_2"] = proofOk;
                var proof = LinkProtocol.ServerProof(goodProof ? token : new byte[32], serverNonce, clientNonce);
                socket.Send(LinkProtocol.Encode(new Dictionary<string, object> { ["type"] = "welcome", ["proof"] = proof }));
                var body = Encoding.UTF8.GetBytes("{\"type\":\"scene\",\"asset\":\"Assets/a.glb\",\"version\":1,\"bytes\":5}");
                var frame = new byte[4 + body.Length + 5];
                frame[3] = (byte)body.Length;
                Buffer.BlockCopy(body, 0, frame, 4, body.Length);
                Buffer.BlockCopy(Encoding.ASCII.GetBytes("glTF!"), 0, frame, 4 + body.Length, 5);
                try
                {
                    socket.Send(frame, 0, 7, SocketFlags.None);
                    Thread.Sleep(100);
                    socket.Send(frame, 7, frame.Length - 7, SocketFlags.None);
                    Thread.Sleep(300);
                }
                catch (SocketException) { }   // The client hung up on a wrong proof.
            }
        });
        server.Start();
        var client = new LinkClient(new Dictionary<string, object> { ["engine"] = LinkProtocol.Engine }, () => record);
        var count = 0;
        client.Message += (header, payload) => count += payload.Length == 5 ? 1 : 100;
        client.EnsureConnected();
        var deadline = DateTime.UtcNow.AddSeconds(5);
        while (DateTime.UtcNow < deadline && client.State != LinkState.Off && count == 0)
        {
            client.Poll();
            Thread.Sleep(10);
        }
        server.Join(2000);
        listener.Stop();
        File.Delete(record);
        messages = count;
        var state = client.State;
        client.Close();
        return state;
    }

    // A real Angelo ----------------------------------------------------------------------------------------------

    static void EndToEnd(string project, string asset, double seconds, string engine)
    {
        var wire = asset;
        var events = new List<object>();
        var scenes = new List<object>();
        var phase = "connecting";
        var client = new LinkClient(new Dictionary<string, object>
        {
            ["engine"] = engine, ["unity"] = "harness", ["project"] = project,
            ["project_name"] = "Angelo Link Unity E2E", ["plugin"] = "harness",
        });
        client.Message += (header, payload) =>
        {
            var kind = LinkProtocol.Text(header, "type");
            events.Add(kind);
            switch (kind)
            {
                case "pending": phase = "pending"; break;
                case "opened": phase = "opened"; break;
                case "refused":
                    phase = "refused";
                    Checks["refused"] = LinkProtocol.Text(header, "message");
                    Checks["refused_code"] = LinkProtocol.Text(header, "code");
                    break;
                case "scene":
                    var magic = payload.Length >= 12 && Encoding.ASCII.GetString(payload, 0, 4) == "glTF";
                    scenes.Add(new Dictionary<string, object>
                        { ["version"] = LinkProtocol.Number(header, "version"), ["bytes"] = payload.Length, ["glb"] = magic });
                    break;
                case "saved":   // AngeloLink.cs re-imports here; the import watcher then reports "reimported".
                    phase = "saved";
                    client.Request(new Dictionary<string, object> { ["type"] = "status", ["code"] = "reimported", ["asset"] = wire });
                    client.Request(new Dictionary<string, object>
                        { ["type"] = "close", ["id"] = "9", ["project"] = project, ["asset"] = wire });
                    break;
                case "closed": phase = "closed"; break;
            }
        };
        Checks["connect_started"] = client.EnsureConnected();
        if (!(bool)Checks["connect_started"])
            Checks["error"] = client.LastError;
        client.Request(new Dictionary<string, object>
        {
            ["type"] = "open", ["id"] = "2", ["project"] = project, ["project_name"] = "Angelo Link Unity E2E",
            ["asset"] = wire, ["node"] = "Crate",
        });
        var deadline = DateTime.UtcNow.AddSeconds(seconds);
        while (DateTime.UtcNow < deadline && phase != "closed" && phase != "refused" && client.State != LinkState.Off)
        {
            client.Poll();
            Thread.Sleep(10);
        }
        Checks["engine"] = engine;
        Checks["events"] = events;
        Checks["scenes"] = scenes;
        Checks["last_error"] = client.LastError;
        Checks["authenticated"] = events.Count > 0;
        Checks["pending_then_opened"] = events.IndexOf("pending") >= 0 && events.IndexOf("opened") > events.IndexOf("pending");
        Checks["two_or_more_glb_scenes"] = scenes.Count >= 2 && scenes.TrueForAll(item => (bool)((Dictionary<string, object>)item)["glb"]);
        Checks["saved_received"] = events.Contains("saved");
        Checks["ended_and_closed"] = events.Contains("ended") && phase == "closed";
        client.Close();
    }
}
