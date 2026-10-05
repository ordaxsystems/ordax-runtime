using System.Windows;

namespace Ordax.Workbench;

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        var runtime = RuntimeLifecycle.EnsureRunning();
        if (!runtime.Ok)
        {
            MessageBox.Show(
                runtime.Summary,
                "ORDAX Runtime indisponível",
                MessageBoxButton.OK,
                MessageBoxImage.Warning
            );
        }

        base.OnStartup(e);
    }
}
