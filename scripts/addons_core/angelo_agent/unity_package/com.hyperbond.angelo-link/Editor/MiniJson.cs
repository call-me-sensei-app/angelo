// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The small JSON subset the link's headers and Angelo's endpoint record use: objects, arrays, strings, numbers,
// booleans and null. Unity's JsonUtility can't read a free-form object, and the package takes no dependencies.
// No UnityEngine here: the protocol harness (tests/…/unity_link_e2e/Harness.cs) compiles this file outside Unity.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace Hyperbond.AngeloLink
{
    /// <summary>Parses to Dictionary&lt;string, object&gt;, List&lt;object&gt;, string, long, double, bool or
    /// null; writes the same shapes (plus int).</summary>
    public static class MiniJson
    {
        const int MaxDepth = 32;

        public static object Parse(string text)
        {
            var reader = new Parser(text);
            var value = reader.Value(0);
            reader.SkipSpace();
            if (!reader.AtEnd)
                throw new FormatException("Text after the JSON value");
            return value;
        }

        public static Dictionary<string, object> ParseObject(string text)
        {
            return Parse(text) as Dictionary<string, object>
                ?? throw new FormatException("The JSON is not an object");
        }

        public static string Write(object value)
        {
            var builder = new StringBuilder();
            WriteValue(builder, value, 0);
            return builder.ToString();
        }

        static void WriteValue(StringBuilder builder, object value, int depth)
        {
            if (depth > MaxDepth)
                throw new FormatException("JSON nested too deeply");
            switch (value)
            {
                case null: builder.Append("null"); break;
                case bool flag: builder.Append(flag ? "true" : "false"); break;
                case string text: WriteString(builder, text); break;
                case int number: builder.Append(number.ToString(CultureInfo.InvariantCulture)); break;
                case long number: builder.Append(number.ToString(CultureInfo.InvariantCulture)); break;
                case double number:
                    if (double.IsNaN(number) || double.IsInfinity(number))
                        throw new FormatException("JSON numbers are finite");
                    builder.Append(number.ToString("R", CultureInfo.InvariantCulture));
                    break;
                case IDictionary<string, object> map:
                    builder.Append('{');
                    var first = true;
                    foreach (var pair in map)
                    {
                        if (!first) builder.Append(',');
                        first = false;
                        WriteString(builder, pair.Key);
                        builder.Append(':');
                        WriteValue(builder, pair.Value, depth + 1);
                    }
                    builder.Append('}');
                    break;
                case IList<object> list:
                    builder.Append('[');
                    for (var i = 0; i < list.Count; i++)
                    {
                        if (i > 0) builder.Append(',');
                        WriteValue(builder, list[i], depth + 1);
                    }
                    builder.Append(']');
                    break;
                default: throw new FormatException("Can't write " + value.GetType().Name + " as JSON");
            }
        }

        static void WriteString(StringBuilder builder, string text)
        {
            builder.Append('"');
            foreach (var c in text)
            {
                switch (c)
                {
                    case '"': builder.Append("\\\""); break;
                    case '\\': builder.Append("\\\\"); break;
                    case '\n': builder.Append("\\n"); break;
                    case '\r': builder.Append("\\r"); break;
                    case '\t': builder.Append("\\t"); break;
                    default:
                        if (c < 0x20) builder.Append("\\u").Append(((int)c).ToString("x4"));
                        else builder.Append(c);
                        break;
                }
            }
            builder.Append('"');
        }

        sealed class Parser
        {
            readonly string _text;
            int _at;

            public Parser(string text) { _text = text ?? throw new FormatException("No JSON"); }

            public bool AtEnd => _at >= _text.Length;

            public void SkipSpace()
            {
                while (!AtEnd && (_text[_at] == ' ' || _text[_at] == '\t' || _text[_at] == '\n' || _text[_at] == '\r'))
                    _at++;
            }

            char Next()
            {
                if (AtEnd) throw new FormatException("The JSON ends early");
                return _text[_at++];
            }

            void Expect(string word)
            {
                if (string.CompareOrdinal(_text, _at, word, 0, word.Length) != 0)
                    throw new FormatException("Unexpected JSON at " + _at);
                _at += word.Length;
            }

            public object Value(int depth)
            {
                if (depth > MaxDepth) throw new FormatException("JSON nested too deeply");
                SkipSpace();
                if (AtEnd) throw new FormatException("The JSON ends early");
                var c = _text[_at];
                switch (c)
                {
                    case '{': return Object(depth);
                    case '[': return Array(depth);
                    case '"': return String();
                    case 't': Expect("true"); return true;
                    case 'f': Expect("false"); return false;
                    case 'n': Expect("null"); return null;
                    default: return Number();
                }
            }

            Dictionary<string, object> Object(int depth)
            {
                var map = new Dictionary<string, object>();
                _at++;
                SkipSpace();
                if (!AtEnd && _text[_at] == '}') { _at++; return map; }
                while (true)
                {
                    SkipSpace();
                    if (AtEnd || _text[_at] != '"') throw new FormatException("A JSON key is a string");
                    var key = String();
                    SkipSpace();
                    if (Next() != ':') throw new FormatException("Expected ':' in JSON");
                    map[key] = Value(depth + 1);
                    SkipSpace();
                    var c = Next();
                    if (c == '}') return map;
                    if (c != ',') throw new FormatException("Expected ',' or '}' in JSON");
                }
            }

            List<object> Array(int depth)
            {
                var list = new List<object>();
                _at++;
                SkipSpace();
                if (!AtEnd && _text[_at] == ']') { _at++; return list; }
                while (true)
                {
                    list.Add(Value(depth + 1));
                    SkipSpace();
                    var c = Next();
                    if (c == ']') return list;
                    if (c != ',') throw new FormatException("Expected ',' or ']' in JSON");
                }
            }

            string String()
            {
                _at++;   // The opening quote.
                var builder = new StringBuilder();
                while (true)
                {
                    var c = Next();
                    if (c == '"') return builder.ToString();
                    if (c < 0x20) throw new FormatException("A control character in a JSON string");
                    if (c != '\\') { builder.Append(c); continue; }
                    var escape = Next();
                    switch (escape)
                    {
                        case '"': builder.Append('"'); break;
                        case '\\': builder.Append('\\'); break;
                        case '/': builder.Append('/'); break;
                        case 'b': builder.Append('\b'); break;
                        case 'f': builder.Append('\f'); break;
                        case 'n': builder.Append('\n'); break;
                        case 'r': builder.Append('\r'); break;
                        case 't': builder.Append('\t'); break;
                        case 'u':
                            if (_at + 4 > _text.Length) throw new FormatException("A short \\u escape");
                            builder.Append((char)Convert.ToInt32(_text.Substring(_at, 4), 16));
                            _at += 4;
                            break;
                        default: throw new FormatException("An unknown JSON escape");
                    }
                }
            }

            object Number()
            {
                var start = _at;
                while (!AtEnd && "+-0123456789.eE".IndexOf(_text[_at]) >= 0)
                    _at++;
                var token = _text.Substring(start, _at - start);
                if (token.Length == 0) throw new FormatException("Unexpected JSON at " + start);
                if (long.TryParse(token, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var whole))
                    return whole;
                if (double.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out var real)
                        && !double.IsInfinity(real))
                    return real;
                throw new FormatException("A bad JSON number");
            }
        }
    }
}
