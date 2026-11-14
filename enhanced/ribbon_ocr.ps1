param([Parameter(Mandatory=$true)][string]$Image)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Receive-WinRT($Operation, [Type]$Type) {
    $task = $asTask.MakeGenericMethod($Type).Invoke($null, @($Operation))
    return $task.GetAwaiter().GetResult()
}
$file = Receive-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Image)) ([Windows.Storage.StorageFile])
$stream = Receive-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode,Windows.Storage,ContentType=WindowsRuntime]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
try {
    $decoder = Receive-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Receive-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    try {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
        if (!$engine) { throw 'Windows OCR language is unavailable' }
        $result = Receive-WinRT ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult,Windows.Media.Ocr,ContentType=WindowsRuntime])
        $lines = @(
            foreach ($line in $result.Lines) {
                ,@(
                    foreach ($word in $line.Words) {
                        $r = $word.BoundingRect
                        @{text=$word.Text; left=$r.X; top=$r.Y; right=$r.X+$r.Width; bottom=$r.Y+$r.Height}
                    }
                )
            }
        )
        @{lines=$lines} | ConvertTo-Json -Depth 6 -Compress
    } finally { $bitmap.Dispose() }
} finally { $stream.Dispose() }
