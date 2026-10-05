using System.IO;
using System.ComponentModel;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;

namespace Ordax.Workbench;

public partial class MainWindow : Window
{
    private sealed record ProviderDefinition(string Id, string Label, string Url);

    private readonly DispatcherTimer _runtimeTimer = new() { Interval = TimeSpan.FromSeconds(5) };
    private readonly List<ProviderDefinition> _providers = new();
    private StudioBridgeClient? _bridge;
    private readonly SemaphoreSlim _viewInitLock = new(1, 1);
    private bool _ready;
    private bool _refreshingStatus;
    private bool _studioViewReady;
    private bool _providerViewReady;
    private bool _previewViewReady;
    private bool _browserViewReady;

    public MainWindow()
    {
        InitializeComponent();
        Loaded += MainWindow_Loaded;
        Closing += MainWindow_Closing;
        _runtimeTimer.Tick += async (_, _) => await RefreshWorkbenchStateAsync();
    }

    private async void MainWindow_Loaded(object sender, RoutedEventArgs e)
    {
        try
        {
            LoadProviders();
            _bridge = new StudioBridgeClient(LogActivity);
            await _bridge.StartAsync();

            // The shared Studio surface is the product. Auxiliary WebViews are
            // intentionally lazy so a hidden provider/browser/preview can never
            // block project startup.
            await EnsureStudioViewAsync();

            _ready = true;
            await RefreshWorkbenchStateAsync();
            _runtimeTimer.Start();
            StatusText.Text = "Workbench pronta";
        }
        catch (Exception error)
        {
            StatusText.Text = $"Falha ao iniciar: {error.Message}";
            LogActivity(error.ToString());
        }
    }

    private async Task InitializeViewAsync(WebView2CompositionControl view, string profile)
    {
        var profileRoot = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "OrdaX", "Workbench", "WebView2", profile
        );
        Directory.CreateDirectory(profileRoot);
        view.CreationProperties = new CoreWebView2CreationProperties
        {
            UserDataFolder = profileRoot,
        };
        await view.EnsureCoreWebView2Async();
        view.CoreWebView2.Settings.AreDevToolsEnabled = true;
        view.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
    }

    private async Task EnsureStudioViewAsync()
    {
        if (_studioViewReady)
            return;
        await _viewInitLock.WaitAsync();
        try
        {
            if (_studioViewReady)
                return;
            await InitializeViewAsync(StudioView, "studio");
            ConfigureStudioView();
            _studioViewReady = true;
        }
        finally
        {
            _viewInitLock.Release();
        }
    }

    private async Task EnsureProviderViewAsync()
    {
        if (_providerViewReady)
            return;
        await _viewInitLock.WaitAsync();
        try
        {
            if (_providerViewReady)
                return;
            await InitializeViewAsync(ProviderView, "provider");
            ConfigureProviderView();
            _providerViewReady = true;
            if (ProviderSelector.SelectedIndex < 0)
                ProviderSelector.SelectedIndex = 0;
        }
        finally
        {
            _viewInitLock.Release();
        }
    }

    private async Task EnsurePreviewViewAsync()
    {
        if (_previewViewReady)
            return;
        await _viewInitLock.WaitAsync();
        try
        {
            if (_previewViewReady)
                return;
            await InitializeViewAsync(PreviewView, "preview-visible");
            _previewViewReady = true;
        }
        finally
        {
            _viewInitLock.Release();
        }
    }

    private async Task EnsureBrowserViewAsync()
    {
        if (_browserViewReady)
            return;
        await _viewInitLock.WaitAsync();
        try
        {
            if (_browserViewReady)
                return;
            await InitializeViewAsync(WorkbenchBrowserView, "user-browser");
            ConfigureWorkbenchBrowser();
            _browserViewReady = true;
        }
        finally
        {
            _viewInitLock.Release();
        }
    }

    private void ConfigureProviderView()
    {
        ProviderView.CoreWebView2.SourceChanged += (_, _) =>
        {
            ProviderAddress.Text = ProviderView.Source?.ToString() ?? "";
        };
        ProviderView.CoreWebView2.NewWindowRequested += (_, args) =>
        {
            args.Handled = true;
            Navigate(ProviderView, args.Uri);
        };
    }

    private void ConfigureStudioView()
    {
        var assets = LocateStudioAssets();
        StudioView.CoreWebView2.SetVirtualHostNameToFolderMapping(
            "ordax.local",
            assets,
            CoreWebView2HostResourceAccessKind.Allow
        );
        StudioView.CoreWebView2.WebMessageReceived += StudioWebMessageReceived;
        StudioView.Source = new Uri("https://ordax.local/studio_product.html");
    }

    private void ConfigureWorkbenchBrowser()
    {
        WorkbenchBrowserView.CoreWebView2.NewWindowRequested += (_, args) =>
        {
            args.Handled = true;
            Navigate(WorkbenchBrowserView, args.Uri);
        };
        WorkbenchBrowserView.CoreWebView2.SourceChanged += (_, _) =>
        {
            WorkbenchBrowserAddress.Text = WorkbenchBrowserView.Source?.ToString() ?? "";
        };
        WorkbenchBrowserAddress.Text = "http://127.0.0.1:5173/";
    }

    private async void StudioWebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        if (_bridge is null)
            return;

        string? requestId = null;
        try
        {
            using var document = JsonDocument.Parse(e.WebMessageAsJson);
            var root = document.RootElement;
            if (!root.TryGetProperty("type", out var type) || type.GetString() != "ordax-rpc")
                return;
            requestId = root.GetProperty("id").GetString();
            var method = root.GetProperty("method").GetString() ?? "";
            var args = root.TryGetProperty("args", out var argsElement)
                ? argsElement.Clone()
                : JsonSerializer.SerializeToElement(Array.Empty<object>());

            var raw = await _bridge.CallRawAsync(method, args, requestId);
            StudioView.CoreWebView2.PostWebMessageAsJson(raw);
        }
        catch (Exception error)
        {
            var response = JsonSerializer.Serialize(new
            {
                id = requestId,
                error = $"{error.GetType().Name}: {error.Message}",
            });
            StudioView.CoreWebView2.PostWebMessageAsJson(response);
            LogActivity($"RPC Studio falhou: {error.Message}");
        }
    }

    private void LoadProviders()
    {
        _providers.Clear();
        _providers.AddRange(new[]
        {
            new ProviderDefinition("chatgpt", "ChatGPT", "https://chatgpt.com/"),
            new ProviderDefinition("grok", "Grok", "https://grok.com/"),
            new ProviderDefinition("claude", "Claude", "https://claude.ai/new"),
            new ProviderDefinition("gemini", "Gemini", "https://gemini.google.com/app"),
            new ProviderDefinition("custom", "Local / Custom", "http://127.0.0.1:3000/"),
        });
        ProviderSelector.ItemsSource = _providers;
        ProviderSelector.DisplayMemberPath = nameof(ProviderDefinition.Label);
    }

    private void ProviderSelector_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_ready || ProviderSelector.SelectedItem is not ProviderDefinition provider)
            return;
        ProviderAddress.Text = provider.Url;
        Navigate(ProviderView, provider.Url);
        LogActivity($"Provider visível: {provider.Label}. O ORDAX não automatiza este WebView.");
    }

    private void ProviderBack_Click(object sender, RoutedEventArgs e)
    {
        if (ProviderView.CoreWebView2?.CanGoBack == true)
            ProviderView.CoreWebView2.GoBack();
    }

    private void ProviderForward_Click(object sender, RoutedEventArgs e)
    {
        if (ProviderView.CoreWebView2?.CanGoForward == true)
            ProviderView.CoreWebView2.GoForward();
    }

    private void ProviderRefresh_Click(object sender, RoutedEventArgs e) => ProviderView.CoreWebView2?.Reload();

    private void ProviderAddress_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter)
            Navigate(ProviderView, ProviderAddress.Text);
    }

    private void WorkbenchBrowserAddress_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter)
            Navigate(WorkbenchBrowserView, WorkbenchBrowserAddress.Text);
    }

    private void WorkbenchBrowserGo_Click(object sender, RoutedEventArgs e) =>
        Navigate(WorkbenchBrowserView, WorkbenchBrowserAddress.Text);

    private async void WorkTabs_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_ready || WorkTabs.SelectedItem is not TabItem tab)
            return;
        if (Equals(tab.Header, "Web IA"))
            await EnsureProviderViewAsync();
        else if (Equals(tab.Header, "Preview"))
        {
            await EnsurePreviewViewAsync();
            await RefreshPreviewAsync();
        }
        else if (Equals(tab.Header, "Browser"))
            await EnsureBrowserViewAsync();
        else if (Equals(tab.Header, "Execuções"))
            await RefreshExecutionStateAsync();
        else if (Equals(tab.Header, "Diagnóstico"))
            await RefreshWorkbenchStateAsync();
    }

    private async void RefreshPreview_Click(object sender, RoutedEventArgs e) => await RefreshPreviewAsync();

    private async void StartPreview_Click(object sender, RoutedEventArgs e)
    {
        if (_bridge is null)
            return;
        try
        {
            var result = await _bridge.CallResultAsync("preview_start");
            LogApiResult("Preview iniciado", result);
            await RefreshPreviewAsync();
            await RefreshExecutionStateAsync();
        }
        catch (Exception error)
        {
            LogActivity($"Falha ao iniciar preview: {error.Message}");
        }
    }

    private async void CapturePreview_Click(object sender, RoutedEventArgs e)
    {
        if (_bridge is null)
            return;
        try
        {
            var result = await _bridge.CallResultAsync("preview_capture");
            LogApiResult("Captura solicitada", result);
            await RefreshPreviewAsync();
            await RefreshExecutionStateAsync();
        }
        catch (Exception error)
        {
            LogActivity($"Falha ao capturar preview: {error.Message}");
        }
    }

    private async void RefreshExecutions_Click(object sender, RoutedEventArgs e) => await RefreshExecutionStateAsync();

    private async Task RefreshPreviewAsync()
    {
        if (_bridge is null || PreviewView.CoreWebView2 is null)
            return;

        try
        {
            var status = await _bridge.CallResultAsync("preview_status");
            if (TryData(status, out var data) &&
                data.TryGetProperty("url", out var urlElement) &&
                Uri.TryCreate(urlElement.GetString(), UriKind.Absolute, out var url) &&
                IsWebUri(url))
            {
                PreviewAddress.Text = url.ToString();
                PreviewView.Source = url;
                return;
            }

            var image = await _bridge.CallResultAsync("preview_image");
            if (TryData(image, out var imageData) &&
                imageData.TryGetProperty("src", out var srcElement))
            {
                var src = srcElement.GetString() ?? "";
                var encoded = JavaScriptEncoder.Default.Encode(src);
                PreviewAddress.Text = "Captura visual do projeto";
                PreviewView.CoreWebView2.NavigateToString(
                    $"<!doctype html><html><body style='margin:0;background:#040a11;display:grid;place-items:center;height:100vh'>" +
                    $"<img src=\"{encoded}\" style='max-width:100%;max-height:100%;object-fit:contain'></body></html>"
                );
                return;
            }

            PreviewAddress.Text = "Nenhum preview disponível";
            PreviewView.CoreWebView2.NavigateToString(
                "<!doctype html><html><body style='margin:0;background:#040a11;color:#8e9db4;font-family:Segoe UI;display:grid;place-items:center;height:100vh'>Nenhum preview disponível.</body></html>"
            );
        }
        catch (Exception error)
        {
            PreviewAddress.Text = $"Preview indisponível: {error.Message}";
            LogActivity($"Preview: {error.Message}");
        }
    }

    private async Task RefreshWorkbenchStateAsync()
    {
        if (_refreshingStatus)
            return;
        _refreshingStatus = true;
        try
        {
            await RefreshRuntimeStateAsync();
            await RefreshExecutionStateAsync();
        }
        finally
        {
            _refreshingStatus = false;
        }
    }

    private async Task RefreshExecutionStateAsync()
    {
        if (_bridge is null)
            return;

        try
        {
            var result = await _bridge.CallResultAsync("execution_status");
            if (!TryData(result, out var data))
                return;

            var builder = new StringBuilder();
            var project = data.TryGetProperty("project", out var projectElement)
                ? projectElement.GetString() ?? "projeto"
                : "projeto";

            var runningProcesses = 0;
            var runningBrowsers = 0;
            builder.AppendLine($"PROJETO  {project}");
            builder.AppendLine();

            if (data.TryGetProperty("preview", out var preview) && preview.ValueKind == JsonValueKind.Object)
            {
                var previewUrl = preview.TryGetProperty("url", out var previewUrlElement)
                    ? previewUrlElement.GetString()
                    : null;
                var mode = preview.TryGetProperty("mode", out var modeElement)
                    ? modeElement.GetString()
                    : null;
                var runtime = preview.TryGetProperty("runtime", out var runtimeElement) &&
                              runtimeElement.ValueKind == JsonValueKind.Object
                    ? runtimeElement
                    : default;
                var previewRunning = runtime.ValueKind == JsonValueKind.Object &&
                                     runtime.TryGetProperty("running", out var runningElement) &&
                                     runningElement.ValueKind == JsonValueKind.True;
                builder.AppendLine($"PREVIEW  {(previewRunning ? "RUNNING" : "IDLE")}  {mode ?? "-"}  {previewUrl ?? "-"}");
            }

            builder.AppendLine();
            builder.AppendLine("PROCESSOS");
            if (data.TryGetProperty("processes", out var processes) && processes.ValueKind == JsonValueKind.Array)
            {
                foreach (var process in processes.EnumerateArray())
                {
                    var running = process.TryGetProperty("running", out var runningElement) &&
                                  runningElement.ValueKind == JsonValueKind.True;
                    if (running)
                        runningProcesses++;
                    var state = process.TryGetProperty("state", out var stateElement) ? stateElement.GetString() : null;
                    var pid = process.TryGetProperty("child_pid", out var childPid) && childPid.ValueKind == JsonValueKind.Number
                        ? childPid.GetInt32()
                        : process.TryGetProperty("manager_pid", out var managerPid) && managerPid.ValueKind == JsonValueKind.Number
                            ? managerPid.GetInt32()
                            : 0;
                    var argv = "";
                    if (process.TryGetProperty("argv", out var argvElement) && argvElement.ValueKind == JsonValueKind.Array)
                        argv = string.Join(" ", argvElement.EnumerateArray().Select(item => item.GetString() ?? ""));
                    builder.AppendLine($"  {(running ? "●" : "○")} PID {pid,-6} {state ?? "-",-10} {argv}");
                }
            }
            if (runningProcesses == 0)
                builder.AppendLine("  nenhum processo persistente ativo");

            builder.AppendLine();
            builder.AppendLine("BROWSERS GERENCIADOS PELO AGENTE");
            if (data.TryGetProperty("browsers", out var browsers) && browsers.ValueKind == JsonValueKind.Array)
            {
                foreach (var browser in browsers.EnumerateArray())
                {
                    var running = browser.TryGetProperty("running", out var runningElement) &&
                                  runningElement.ValueKind == JsonValueKind.True;
                    if (running)
                        runningBrowsers++;
                    var title = browser.TryGetProperty("title", out var titleElement) ? titleElement.GetString() : null;
                    var url = browser.TryGetProperty("url", out var urlElement) ? urlElement.GetString() : null;
                    builder.AppendLine($"  {(running ? "●" : "○")} {title ?? "(sem título)"}  {url ?? "-"}");
                }
            }
            if (runningBrowsers == 0)
                builder.AppendLine("  nenhum browser gerenciado ativo");

            Title = string.IsNullOrWhiteSpace(project) ? "ORDAX Studio" : $"ORDAX Studio — {project}";
            ProjectCardState.Text = $"{project} · {runningProcesses} processo(s) · {runningBrowsers} browser(s) gerenciado(s)";
            ExecutionState.Text = builder.ToString();
        }
        catch (Exception error)
        {
            ProjectCardState.Text = "Monitor do Runtime indisponível";
            ExecutionState.Text = error.Message;
        }
    }

    private void LogApiResult(string prefix, JsonElement result)
    {
        var summary = result.ValueKind == JsonValueKind.Object &&
                      result.TryGetProperty("summary", out var summaryElement)
            ? summaryElement.GetString()
            : null;
        LogActivity(string.IsNullOrWhiteSpace(summary) ? prefix : $"{prefix}: {summary}");
    }

    private async Task RefreshRuntimeStateAsync()
    {
        if (_bridge is null)
            return;
        try
        {
            var result = await _bridge.CallResultAsync("product_status");
            if (!TryData(result, out var data))
                return;

            var device = data.TryGetProperty("device_agent", out var deviceElement) ? deviceElement : default;
            var remote = data.TryGetProperty("remote_mcp", out var remoteElement) ? remoteElement : default;
            var deviceOk = device.ValueKind == JsonValueKind.Object &&
                           device.TryGetProperty("ok", out var deviceOkElement) &&
                           deviceOkElement.ValueKind == JsonValueKind.True;
            var remoteOk = remote.ValueKind == JsonValueKind.Object &&
                           remote.TryGetProperty("ok", out var remoteOkElement) &&
                           remoteOkElement.ValueKind == JsonValueKind.True;

            var computer = data.TryGetProperty("computer_control", out var computerElement) ? computerElement : default;
            var computerOk = computer.ValueKind == JsonValueKind.Object &&
                             computer.TryGetProperty("ok", out var computerOkElement) &&
                             computerOkElement.ValueKind == JsonValueKind.True;
            var actionCount = computer.ValueKind == JsonValueKind.Object &&
                              computer.TryGetProperty("action_count", out var actionCountElement) &&
                              actionCountElement.ValueKind == JsonValueKind.Number
                ? actionCountElement.GetInt32()
                : 0;
            var computerSummary = computer.ValueKind == JsonValueKind.Object &&
                                  computer.TryGetProperty("summary", out var computerSummaryElement)
                ? computerSummaryElement.GetString() ?? "Computer Control indisponível"
                : "Computer Control indisponível";

            var transportState = device.ValueKind == JsonValueKind.Object &&
                                 device.TryGetProperty("transport_state", out var transportStateElement)
                ? transportStateElement.GetString() ?? "unknown"
                : "unknown";
            var runtimeState = device.ValueKind == JsonValueKind.Object &&
                               device.TryGetProperty("runtime_state", out var runtimeStateElement)
                ? runtimeStateElement.GetString() ?? "unknown"
                : "unknown";
            var transportKnown = transportState is not ("" or "unknown");
            var transportConnected = transportState == "connected" || !transportKnown;
            RuntimeState.Text = $"Runtime: {(deviceOk ? runtimeState : "local/degradado")} · MCP: {(remoteOk ? "online" : "verificando")} · Link: {transportState}";
            DeviceCardState.Text = deviceOk
                ? $"Device Agent {runtimeState} · transporte {transportState}"
                : "Device Agent local ou degradado";
            McpCardState.Text = remoteOk
                ? transportConnected ? "Remote MCP + dispositivo conectados" : $"Remote MCP online · dispositivo {transportState}"
                : "Remote MCP verificando";
            ComputerControlState.Text = computerSummary;
            ComputerRailState.Text = computerOk ? "Computer Control disponível" : "Computer Control parcial";
            ActionCountState.Text = actionCount > 0 ? actionCount.ToString() : "—";
        }
        catch (Exception error)
        {
            RuntimeState.Text = "Runtime: bridge indisponível";
            DeviceCardState.Text = "Device Agent indisponível";
            McpCardState.Text = "Remote MCP indisponível";
            ComputerControlState.Text = "Computer Control indisponível";
            ComputerRailState.Text = "Computer Control indisponível";
            ActionCountState.Text = "—";
            LogActivity($"Status: {error.Message}");
        }
    }

    private static bool TryData(JsonElement result, out JsonElement data)
    {
        data = default;
        return result.ValueKind == JsonValueKind.Object &&
               result.TryGetProperty("ok", out var ok) &&
               ok.ValueKind == JsonValueKind.True &&
               result.TryGetProperty("data", out data);
    }

    private static bool IsWebUri(Uri uri) =>
        uri.Scheme.Equals(Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase) ||
        uri.Scheme.Equals(Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase);

    private void Navigate(WebView2CompositionControl view, string? raw)
    {
        if (!Uri.TryCreate(raw?.Trim(), UriKind.Absolute, out var uri) || !IsWebUri(uri))
        {
            StatusText.Text = "Use uma URL http:// ou https:// válida.";
            return;
        }
        view.Source = uri;
        StatusText.Text = uri.Host;
    }

    private static string LocateStudioAssets()
    {
        var configured = Environment.GetEnvironmentVariable("ORDAX_STUDIO_ASSETS_DIR");
        if (!string.IsNullOrWhiteSpace(configured) && Directory.Exists(configured))
            return Path.GetFullPath(configured);

        var candidates = new[]
        {
            Path.Combine(AppContext.BaseDirectory, "runtime", "Lib", "site-packages", "ordax_studio"),
            Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "runtime", "Lib", "site-packages", "ordax_studio")),
            Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "ordax_studio")),
        };
        foreach (var candidate in candidates)
            if (File.Exists(Path.Combine(candidate, "studio_product.html")))
                return candidate;

        throw new DirectoryNotFoundException("Could not locate ORDAX Studio web assets.");
    }

    private void LogActivity(string text)
    {
        Dispatcher.Invoke(() =>
        {
            ActivityLog.AppendText($"[{DateTime.Now:HH:mm:ss}] {text}{Environment.NewLine}");
            ActivityLog.ScrollToEnd();
        });
    }

    private async void MainWindow_Closing(object? sender, CancelEventArgs e)
    {
        _runtimeTimer.Stop();
        if (_bridge is not null)
            await _bridge.DisposeAsync();
    }
}
