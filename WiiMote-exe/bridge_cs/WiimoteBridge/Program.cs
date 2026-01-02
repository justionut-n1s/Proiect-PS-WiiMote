using System;
using System.Net;
using System.Net.Sockets;
using System.Text;
using Nefarius.ViGEm.Client;
using Nefarius.ViGEm.Client.Targets;
using Nefarius.ViGEm.Client.Targets.Xbox360;


class Program
{
    static void Main()
    {
        Console.WriteLine("Starting ViGEm bridge...");

        // ViGEm client
        var client = new ViGEmClient();

        // Create Xbox 360 controller
        IXbox360Controller pad = client.CreateXbox360Controller();
        pad.Connect();

        Console.WriteLine("Xbox 360 controller connected");
        Console.WriteLine("Listening UDP on 127.0.0.1:9876");

        // UDP listener
        UdpClient udp = new UdpClient(9876);

        // Axis state
        short lx = 0;
        short ly = 0;

        while (true)
        {
            IPEndPoint ep = new IPEndPoint(IPAddress.Any, 0);
            byte[] data = udp.Receive(ref ep);
            string msg = Encoding.UTF8.GetString(data);

            Console.WriteLine("RECV: " + msg);

            // ============================
            // RESET BUTTONS
            // ============================

            pad.SetButtonState(Xbox360Button.A, false);
            pad.SetButtonState(Xbox360Button.B, false);
            pad.SetButtonState(Xbox360Button.X, false);
            pad.SetButtonState(Xbox360Button.Y, false);

            pad.SetButtonState(Xbox360Button.Start, false);
            pad.SetButtonState(Xbox360Button.Back, false);
            pad.SetButtonState(Xbox360Button.Guide, false);

            pad.SetButtonState(Xbox360Button.Up, false);
            pad.SetButtonState(Xbox360Button.Down, false);
            pad.SetButtonState(Xbox360Button.Left, false);
            pad.SetButtonState(Xbox360Button.Right, false);

            // ============================
            // PARSE MESSAGE
            // ============================

            var parts = msg.Split(',');
            foreach (var p in parts)
            {
                var kv = p.Split('=');
                if (kv.Length != 2) continue;

                string key = kv[0].Trim().ToUpper();
                string value = kv[1].Trim();

                // -------- AXES --------
                if (key == "RX")
                {
                    short.TryParse(value, out lx);
                    continue;
                }
                if (key == "RY")
                {
                    short.TryParse(value, out ly);
                    continue;
                }

                // -------- BUTTONS --------
                bool pressed = value == "1";

                switch (key)
                {
                    case "A": pad.SetButtonState(Xbox360Button.A, pressed); break;
                    case "B": pad.SetButtonState(Xbox360Button.B, pressed); break;
                    case "X": pad.SetButtonState(Xbox360Button.X, pressed); break;
                    case "Y": pad.SetButtonState(Xbox360Button.Y, pressed); break;

                    case "START": pad.SetButtonState(Xbox360Button.Start, pressed); break;
                    case "BACK": pad.SetButtonState(Xbox360Button.Back, pressed); break;
                    case "GUIDE": pad.SetButtonState(Xbox360Button.Guide, pressed); break;

                    case "UP": pad.SetButtonState(Xbox360Button.Up, pressed); break;
                    case "DOWN": pad.SetButtonState(Xbox360Button.Down, pressed); break;
                    case "LEFT": pad.SetButtonState(Xbox360Button.Left, pressed); break;
                    case "RIGHT": pad.SetButtonState(Xbox360Button.Right, pressed); break;
                }
            }

            // ============================
            // APPLY AXES (LEFT STICK)
            // ============================

            pad.SetAxisValue(Xbox360Axis.LeftThumbX, lx);
            pad.SetAxisValue(Xbox360Axis.LeftThumbY, ly);
        }
    }
}
