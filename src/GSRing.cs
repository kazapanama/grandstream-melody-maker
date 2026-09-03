// GSRing.exe — самодостатній запускач GSRing Studio.
// Сторінка вшита в exe як ресурс; програма розпаковує її в %LOCALAPPDATA%\GSRing
// і відкриває у вікні Edge/Chrome в режимі застосунку (без адресного рядка й вкладок).
// Збірка: build.cmd (csc.exe входить до складу Windows, нічого ставити не треба).

using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using Microsoft.Win32;

static class GSRing
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern int MessageBoxW(IntPtr hWnd, string text, string caption, uint type);

    [STAThread]
    static int Main(string[] args)
    {
        try
        {
            string dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "GSRing");
            Directory.CreateDirectory(dir);
            string html = Path.Combine(dir, "index.html");

            using (Stream src = Assembly.GetExecutingAssembly().GetManifestResourceStream("index.html"))
            using (Stream dst = File.Create(html))
                src.CopyTo(dst);

            if (args.Length > 0 && args[0] == "/extract")
                return 0;                       // режим самоперевірки: тільки розпакувати

            string url = new Uri(html).AbsoluteUri;
            string browser = FindBrowser();
            if (browser != null)
                Process.Start(new ProcessStartInfo(browser,
                    "--app=\"" + url + "\" --window-size=1180,960")
                { UseShellExecute = false });
            else
                Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            return 0;
        }
        catch (Exception e)
        {
            MessageBoxW(IntPtr.Zero,
                "Не вдалося запустити GSRing Studio.\n\n" + e.Message, "GSRing", 0x10);
            return 1;
        }
    }

    // Edge є в кожній Windows 10/11; Chrome — запасний варіант.
    static string FindBrowser()
    {
        string pf = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        string pf86 = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string[] candidates =
        {
            Path.Combine(pf86,  @"Microsoft\Edge\Application\msedge.exe"),
            Path.Combine(pf,    @"Microsoft\Edge\Application\msedge.exe"),
            Path.Combine(pf,    @"Google\Chrome\Application\chrome.exe"),
            Path.Combine(pf86,  @"Google\Chrome\Application\chrome.exe"),
            Path.Combine(local, @"Google\Chrome\Application\chrome.exe"),
        };
        foreach (string c in candidates)
            if (File.Exists(c)) return c;

        foreach (string name in new[] { "msedge.exe", "chrome.exe" })
        {
            object v = Registry.GetValue(
                @"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\" + name,
                "", null);
            if (v != null && File.Exists(v.ToString())) return v.ToString();
        }
        return null;
    }
}
