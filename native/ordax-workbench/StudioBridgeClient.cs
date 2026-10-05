using System.IO;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;

namespace Ordax.Workbench;

internal sealed class StudioBridgeClient : IAsyncDisposable
{
    private readonly ConcurrentDictionary<string, TaskCompletionSource<string>> _pending = new();
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private readonly Action<string> _log;
    private Process? _process;
    private CancellationTokenSource? _lifetime;

    public StudioBridgeClient(Action<string> log)
    {
        _log = log;
    }

    public async Task StartAsync(CancellationToken cancellationToken = default)
    {
        if (_process is { HasExited: false })
            return;

        var python = ResolvePython();
        var start = new ProcessStartInfo
        {
            FileName = python,
            WorkingDirectory = AppContext.BaseDirectory,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        start.ArgumentList.Add("-u");
        start.ArgumentList.Add("-m");
        start.ArgumentList.Add("ordax_studio.workbench_bridge");

        _process = Process.Start(start) ?? throw new InvalidOperationException("Could not start ORDAX workbench bridge.");
        _lifetime = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _ = Task.Run(() => ReadStdoutAsync(_lifetime.Token), _lifetime.Token);
        _ = Task.Run(() => ReadStderrAsync(_lifetime.Token), _lifetime.Token);
        await Task.Yield();
        _log($"Bridge local iniciado com {Path.GetFileName(python)}.");
    }

    public async Task<string> CallRawAsync(string method, object? args = null, string? requestId = null, CancellationToken cancellationToken = default)
    {
        if (_process is null || _process.HasExited)
            await StartAsync(cancellationToken);

        requestId ??= Guid.NewGuid().ToString("N");
        var tcs = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        if (!_pending.TryAdd(requestId, tcs))
            throw new InvalidOperationException("Duplicate ORDAX workbench request id.");

        var payload = JsonSerializer.Serialize(new
        {
            id = requestId,
            method,
            args = args ?? Array.Empty<object>(),
        });

        try
        {
            await _writeLock.WaitAsync(cancellationToken);
            try
            {
                await _process!.StandardInput.WriteLineAsync(payload);
                await _process.StandardInput.FlushAsync();
            }
            finally
            {
                _writeLock.Release();
            }

            _log($"→ {method}");
            using var registration = cancellationToken.Register(() => tcs.TrySetCanceled(cancellationToken));
            return await tcs.Task;
        }
        catch
        {
            _pending.TryRemove(requestId, out _);
            throw;
        }
    }

    public async Task<JsonElement> CallResultAsync(string method, object? args = null, CancellationToken cancellationToken = default)
    {
        var raw = await CallRawAsync(method, args, cancellationToken: cancellationToken);
        using var document = JsonDocument.Parse(raw);
        var root = document.RootElement;
        if (root.TryGetProperty("error", out var error))
            throw new InvalidOperationException(error.GetString() ?? "ORDAX workbench bridge error.");
        return root.GetProperty("result").Clone();
    }

    private async Task ReadStdoutAsync(CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested && _process is { HasExited: false })
        {
            var line = await _process.StandardOutput.ReadLineAsync(cancellationToken);
            if (line is null)
                break;
            try
            {
                using var document = JsonDocument.Parse(line);
                var id = document.RootElement.GetProperty("id").ToString();
                if (_pending.TryRemove(id, out var tcs))
                    tcs.TrySetResult(line);
            }
            catch (JsonException)
            {
                _log($"Bridge: {line}");
            }
        }
        FailPending("ORDAX workbench bridge exited.");
    }

    private async Task ReadStderrAsync(CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested && _process is { HasExited: false })
        {
            var line = await _process.StandardError.ReadLineAsync(cancellationToken);
            if (line is null)
                break;
            _log($"Bridge stderr: {line}");
        }
    }

    private static string ResolvePython()
    {
        var configured = Environment.GetEnvironmentVariable("ORDAX_WORKBENCH_PYTHON");
        if (!string.IsNullOrWhiteSpace(configured) && File.Exists(configured))
            return configured!;

        var bundled = Path.Combine(AppContext.BaseDirectory, "runtime", "python.exe");
        if (File.Exists(bundled))
            return bundled;

        var sibling = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "runtime", "python.exe"));
        if (File.Exists(sibling))
            return sibling;

        return "python";
    }

    private void FailPending(string message)
    {
        foreach (var item in _pending.ToArray())
            if (_pending.TryRemove(item.Key, out var tcs))
                tcs.TrySetException(new InvalidOperationException(message));
    }

    public async ValueTask DisposeAsync()
    {
        _lifetime?.Cancel();
        if (_process is { HasExited: false })
        {
            try
            {
                _process.StandardInput.Close();
                await _process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(2));
            }
            catch
            {
                try { _process.Kill(entireProcessTree: true); } catch { }
            }
        }
        _process?.Dispose();
        _lifetime?.Dispose();
        _writeLock.Dispose();
    }
}
