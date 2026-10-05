using System.Diagnostics;
using System.IO;
using System.Threading;

namespace Ordax.Workbench;

internal sealed record RuntimeLifecycleResult(bool Ok, string State, string Summary);

internal static class RuntimeLifecycle
{
    private const string RuntimeMutex = "Local\\ORDAXRuntime";

    public static RuntimeLifecycleResult EnsureRunning()
    {
        if (RuntimeAlreadyRunning())
            return new(true, "already_running", "ORDAX Runtime já está ativo.");

        var executable = LocateRuntimeExecutable();
        if (executable is null)
            return new(false, "missing", "ORDAX Runtime.exe não foi encontrado na instalação atual.");

        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = executable,
                WorkingDirectory = Path.GetDirectoryName(executable)!,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            using var process = Process.Start(startInfo);
            if (process is null)
                return new(false, "start_failed", "O Windows não iniciou o ORDAX Runtime.");

            if (process.WaitForExit(750))
            {
                if (RuntimeAlreadyRunning())
                    return new(true, "already_running", "ORDAX Runtime já foi iniciado por outra instância.");
                return new(false, "exited_early", $"ORDAX Runtime encerrou durante a inicialização (código {process.ExitCode}).");
            }

            return new(true, "started", "ORDAX Runtime iniciado pela Workbench.");
        }
        catch (Exception error)
        {
            return new(false, "start_failed", $"Falha ao iniciar ORDAX Runtime: {error.Message}");
        }
    }

    private static bool RuntimeAlreadyRunning()
    {
        try
        {
            using var mutex = Mutex.OpenExisting(RuntimeMutex);
            return true;
        }
        catch (WaitHandleCannotBeOpenedException)
        {
            return false;
        }
        catch (UnauthorizedAccessException)
        {
            return true;
        }
    }

    private static string? LocateRuntimeExecutable()
    {
        var workbench = Environment.ProcessPath;
        if (string.IsNullOrWhiteSpace(workbench))
            return null;

        var workbenchDirectory = Path.GetDirectoryName(workbench);
        var root = workbenchDirectory is null ? null : Directory.GetParent(workbenchDirectory)?.FullName;
        if (string.IsNullOrWhiteSpace(root))
            return null;

        var candidate = Path.Combine(root, "ORDAX Runtime.exe");
        return File.Exists(candidate) ? candidate : null;
    }
}
