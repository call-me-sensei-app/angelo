// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The connection to Angelo, driven by Poll() from the editor's update loop and never blocking it: reads Angelo's
// endpoint record, connects over loopback, proves the record's token without sending it (HMAC-SHA256 both ways,
// LinkProtocol) and exchanges framed messages. Nothing Angelo sends is trusted until Angelo has proved the token too.
// No UnityEngine here: the protocol harness (tests/…/unity_link_e2e/Harness.cs) drives this class against a real Angelo.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Threading.Tasks;

namespace Hyperbond.AngeloLink
{
    public enum LinkState { Off, Connecting, Authenticating, Ready }

    public sealed class LinkClient
    {
        public const double PingSeconds = 10.0;
        public const double AuthSeconds = 10.0;

        /// <summary>A message from Angelo after authentication (pong excluded): header and payload.</summary>
        public event Action<Dictionary<string, object>, byte[]> Message;
        public event Action<LinkState> StateChanged;

        public LinkState State { get; private set; } = LinkState.Off;
        public string AppPath { get; private set; } = "";   // Angelo's application, from the record.
        public string LastError { get; private set; } = ""; // Why the last attempt failed (English, for the person).

        readonly Dictionary<string, object> _clientInfo;
        readonly Func<string> _recordPath;
        readonly Stopwatch _clock = Stopwatch.StartNew();
        readonly List<Dictionary<string, object>> _queue = new List<Dictionary<string, object>>();
        readonly byte[] _receive = new byte[256 * 1024];
        Socket _socket;
        Task _connecting;
        FrameReader _reader;
        byte[] _outgoing = new byte[0];
        byte[] _token;
        string _serverNonce = "", _clientNonce = "";
        double _since;

        public LinkClient(Dictionary<string, object> clientInfo, Func<string> recordPath = null)
        {
            _clientInfo = clientInfo;
            _recordPath = recordPath ?? LinkProtocol.RecordPath;
        }

        double Now => _clock.Elapsed.TotalSeconds;

        public bool IsReady => State == LinkState.Ready;

        /// <summary>Connect when not connected; false (with LastError) when Angelo isn't listening.</summary>
        public bool EnsureConnected()
        {
            if (State != LinkState.Off)
                return true;
            var record = LinkProtocol.ReadRecord(_recordPath());
            if (record == null)
            {
                LastError = "Angelo isn't listening. In Angelo, turn on the live link from the + menu in the chat bar.";
                return false;
            }
            _token = LinkProtocol.FromHex(LinkProtocol.Text(record, "token"));
            AppPath = LinkProtocol.Text(record, "app");
            _socket = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp) { NoDelay = true };
            _connecting = _socket.ConnectAsync(IPAddress.Loopback, (int)LinkProtocol.Number(record, "port"));
            _reader = new FrameReader();
            _outgoing = new byte[0];
            _serverNonce = _clientNonce = "";
            _since = Now;
            SetState(LinkState.Connecting);
            return true;
        }

        /// <summary>Send a request now, or once the connection is ready.</summary>
        public void Request(Dictionary<string, object> header)
        {
            if (State == LinkState.Ready)
                Send(header);
            else
                _queue.Add(header);
        }

        public void Close()
        {
            if (_socket != null)
            {
                try { _socket.Close(); } catch (SocketException) { }
            }
            _socket = null;
            _connecting = null;
            _reader = null;
            _outgoing = new byte[0];
            _queue.Clear();
            if (State != LinkState.Off)
                SetState(LinkState.Off);
        }

        /// <summary>Call often (EditorApplication.update): connects, authenticates, reads frames, writes and pings.
        /// Never blocks.</summary>
        public void Poll()
        {
            if (_socket == null)
                return;
            try
            {
                if (State == LinkState.Connecting && !Connected())
                    return;
                Read();
                Write();
                if (_socket == null)
                    return;
                if (State == LinkState.Authenticating && Now - _since > AuthSeconds)
                    Fail("Angelo didn't answer.");
                else if (State == LinkState.Ready && Now - _since > PingSeconds)
                {
                    _since = Now;
                    Send(new Dictionary<string, object> { ["type"] = "ping" });
                }
            }
            catch (Exception error) when (error is SocketException || error is ObjectDisposedException
                                          || error is FormatException)
            {
                Fail(State == LinkState.Ready ? "The connection to Angelo closed." : "Couldn't reach Angelo. Is it still open?");
            }
        }

        bool Connected()
        {
            if (!_connecting.IsCompleted)
            {
                if (Now - _since > AuthSeconds)
                    Fail("Angelo didn't answer.");
                return false;
            }
            if (_connecting.IsFaulted || _connecting.IsCanceled)
            {
                Fail("Couldn't reach Angelo. Is it still open?");
                return false;
            }
            _socket.Blocking = false;
            SetState(LinkState.Authenticating);
            return true;
        }

        void Read()
        {
            while (_socket != null && _socket.Available > 0)
            {
                var got = _socket.Receive(_receive, 0, Math.Min(_receive.Length, _socket.Available), SocketFlags.None);
                if (got <= 0)
                    break;
                _reader.Feed(_receive, got);
                while (_reader != null && _reader.TryTake(out var header, out var payload))
                    Receive(header, payload);
            }
            // Readable with nothing to read: Angelo closed the connection.
            if (_socket != null && _socket.Poll(0, SelectMode.SelectRead) && _socket.Available == 0)
                Fail(State == LinkState.Ready ? "Angelo closed the link." : "Angelo didn't accept this connection.");
        }

        void Write()
        {
            if (_socket == null || _outgoing.Length == 0)
                return;
            int sent;
            try
            {
                sent = _socket.Send(_outgoing, 0, _outgoing.Length, SocketFlags.None);
            }
            catch (SocketException error) when (error.SocketErrorCode == SocketError.WouldBlock)
            {
                return;
            }
            var rest = new byte[_outgoing.Length - sent];
            Buffer.BlockCopy(_outgoing, sent, rest, 0, rest.Length);
            _outgoing = rest;
        }

        void Receive(Dictionary<string, object> header, byte[] payload)
        {
            var kind = LinkProtocol.Text(header, "type");
            if (State == LinkState.Authenticating)
            {
                if (kind == "hello" && _serverNonce == "" && LinkProtocol.Text(header, "protocol") == LinkProtocol.Protocol
                        && LinkProtocol.ValidNonce(header.TryGetValue("nonce", out var nonce) ? nonce : null))
                {
                    _serverNonce = (string)nonce;
                    _clientNonce = LinkProtocol.NewNonce();
                    Send(new Dictionary<string, object>
                    {
                        ["type"] = "auth", ["nonce"] = _clientNonce,
                        ["proof"] = LinkProtocol.ClientProof(_token, _serverNonce, _clientNonce), ["client"] = _clientInfo,
                    });
                }
                else if (kind == "welcome" && _serverNonce != "" && LinkProtocol.ProofMatches(
                             LinkProtocol.ServerProof(_token, _serverNonce, _clientNonce),
                             header.TryGetValue("proof", out var proof) ? proof : null))
                {
                    _since = Now;
                    SetState(LinkState.Ready);
                    foreach (var queued in _queue)
                        Send(queued);
                    _queue.Clear();
                }
                else
                {
                    Fail(kind == "error" ? "Angelo didn't accept this connection. Turn the live link off and on in Angelo."
                                         : "Something else answered on Angelo's port.");
                }
                return;
            }
            if (kind == "pong")
                return;
            Message?.Invoke(header, payload);
        }

        void Send(Dictionary<string, object> header)
        {
            if (_socket == null)
                return;
            var frame = LinkProtocol.Encode(header);
            var joined = new byte[_outgoing.Length + frame.Length];
            Buffer.BlockCopy(_outgoing, 0, joined, 0, _outgoing.Length);
            Buffer.BlockCopy(frame, 0, joined, _outgoing.Length, frame.Length);
            _outgoing = joined;
            Write();
        }

        void Fail(string reason)
        {
            LastError = reason;
            Close();
        }

        void SetState(LinkState value)
        {
            State = value;
            StateChanged?.Invoke(value);
        }
    }
}
