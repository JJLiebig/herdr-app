// ConsoleProbe adapted from herdr/scripts/windows_input/Native.cs (Apache-2.0).
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
namespace HerdrInputGauntlet {
    public sealed class ConsoleProbe : IDisposable {
        [StructLayout(LayoutKind.Sequential)] struct Coord { public short X,Y; }
        [StructLayout(LayoutKind.Sequential)] struct SmallRect { public short Left,Top,Right,Bottom; }
        [StructLayout(LayoutKind.Sequential)] struct Info { public Coord Size,Cursor; public ushort Attributes; public SmallRect Window; public Coord Maximum; }
        [StructLayout(LayoutKind.Explicit,Size=20)] struct Record {
            [FieldOffset(0)] public ushort Type;
            [FieldOffset(4)] public int Down;
            [FieldOffset(8)] public ushort Repeat;
            [FieldOffset(10)] public ushort Vk;
            [FieldOffset(12)] public ushort Scan;
            [FieldOffset(14)] public ushort Unicode;
            [FieldOffset(16)] public uint Control;
        }
        [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int kind);
        [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetConsoleMode(IntPtr handle,out uint mode);
        [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetConsoleMode(IntPtr handle,uint mode);
        [DllImport("kernel32.dll")] static extern bool GetConsoleScreenBufferInfo(IntPtr handle,out Info info);
        [DllImport("kernel32.dll")] static extern uint GetConsoleCP();
        [DllImport("kernel32.dll")] static extern bool SetConsoleCP(uint cp);
        [DllImport("kernel32.dll")] static extern bool ReadFile(IntPtr handle,byte[] buffer,uint size,out uint count,IntPtr overlap);
        [DllImport("kernel32.dll")] static extern bool WriteFile(IntPtr handle,byte[] buffer,uint size,out uint count,IntPtr overlap);
        [DllImport("kernel32.dll")] static extern bool ReadConsoleInputW(IntPtr handle,[Out] Record[] records,uint size,out uint count);
        [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
        [DllImport("kernel32.dll")] static extern IntPtr OpenThread(uint access,bool inherit,uint id);
        [DllImport("kernel32.dll")] static extern bool CancelSynchronousIo(IntPtr thread);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        readonly object gate=new object();
        readonly List<byte> bytes=new List<byte>();
        readonly List<long[]> records=new List<long[]>();
        readonly IntPtr input=GetStdHandle(-10), output=GetStdHandle(-11);
        readonly uint original, outputMode, cp;
        readonly bool native;
        volatile bool running=true;
        Thread reader;
        IntPtr threadHandle;
        string error;
        public string Error {
            get { return Volatile.Read(ref error); }
            private set { Volatile.Write(ref error,value); }
        }
        public ConsoleProbe(string mode) {
            if(!GetConsoleMode(input,out original) || !GetConsoleMode(output,out outputMode)) throw new Exception("Probe needs a real console");
            cp=GetConsoleCP(); native=mode=="native";
            try {
                if(!SetConsoleCP(65001) || !SetConsoleMode(output,outputMode|4) || !SetConsoleMode(input,native ? (original & ~0x247u)|0x98u : (original & ~7u)|0x200u)) throw new Win32Exception();
                Print(mode.StartsWith("kitty") ? "\x1b[>"+mode.Substring(5)+"u\x1b[?u" : "");
                reader=new Thread(Read) { IsBackground=true }; reader.Start();
            } catch { SetConsoleMode(input,original); SetConsoleMode(output,outputMode); SetConsoleCP(cp); throw; }
        }
        public static long[] Geometry() {
            Info info; uint mode;
            if(!GetConsoleScreenBufferInfo(GetStdHandle(-11),out info) || !GetConsoleMode(GetStdHandle(-10),out mode)) throw new Exception("Console geometry unavailable");
            return new long[]{ info.Window.Right-info.Window.Left+1,info.Window.Bottom-info.Window.Top+1,mode };
        }
        public static void Print(string text) {
            byte[] data=Encoding.UTF8.GetBytes(text); uint count;
            if(!WriteFile(GetStdHandle(-11),data,(uint)data.Length,out count,IntPtr.Zero) || count!=data.Length) throw new Exception("Console output failed");
        }
        void Read() {
            threadHandle=OpenThread(1,false,GetCurrentThreadId());
            if(threadHandle==IntPtr.Zero) { Error="Cannot acquire reader cancellation handle"; return; }
            try {
                while(running) {
                    if(Count()>1048576) { Error="Probe input limit exceeded"; break; }
                    uint count;
                    if(native) {
                        var batch=new Record[64];
                        if(!ReadConsoleInputW(input,batch,64,out count)) { if(running) Error="ReadConsoleInputW failed"; break; }
                        lock(gate) for(int i=0;i<count;i++) {
                            var r=batch[i];
                            // Preserve every native record, including non-key event type and payload.
                            records.Add(new long[]{r.Type,r.Down,r.Repeat,r.Vk,r.Scan,r.Unicode,r.Control});
                        }
                    } else {
                        var batch=new byte[4096];
                        if(!ReadFile(input,batch,4096,out count,IntPtr.Zero)) { if(running) Error="ReadFile failed"; break; }
                        lock(gate) for(int i=0;i<count;i++) bytes.Add(batch[i]);
                    }
                }
            } catch(Exception e) { Error=e.Message; }
        }
        public void Clear() { lock(gate) { bytes.Clear(); records.Clear(); } }
        public bool ClearIfCount(int expected) {
            lock(gate) {
                if(bytes.Count+records.Count!=expected) {
                    if(!native || bytes.Count!=0 || records.Count<expected) return false;
                    for(int i=expected;i<records.Count;i++)
                        if(records[i][0]!=4 && records[i][0]!=16) return false; // resize/focus between captures
                }
                bytes.Clear(); records.Clear(); return true;
            }
        }
        public string Hex() { lock(gate) return BitConverter.ToString(bytes.ToArray()).Replace("-","").ToLowerInvariant(); }
        public long[][] Records() { lock(gate) return records.ToArray(); }
        public int Count() { lock(gate) return bytes.Count+records.Count; }
        public string[] CleanupErrors { get; private set; } = new string[0];
        public void Dispose() {
            var errors=new List<string>();
            running=false;
            if(threadHandle!=IntPtr.Zero) CancelSynchronousIo(threadHandle);
            if(reader!=null && !reader.Join(1000)) errors.Add("Reader did not stop");
            if(threadHandle!=IntPtr.Zero) CloseHandle(threadHandle);
            try { Print("\x1b[<u\x1b[>4;0m\x1b[?2004l\x1b[?1000l\x1b[?1006l"); }
            catch(Exception e) { errors.Add(e.Message); }
            if(!SetConsoleMode(input,original)) errors.Add("Input-mode restoration failed");
            if(!SetConsoleMode(output,outputMode)) errors.Add("Output-mode restoration failed");
            if(!SetConsoleCP(cp)) errors.Add("Code-page restoration failed");
            uint actual;
            if(!GetConsoleMode(input,out actual) || actual!=original) errors.Add("Input mode differs after restoration");
            if(!GetConsoleMode(output,out actual) || actual!=outputMode) errors.Add("Output mode differs after restoration");
            if(GetConsoleCP()!=cp) errors.Add("Code page differs after restoration");
            CleanupErrors=errors.ToArray();
        }
    }
}
