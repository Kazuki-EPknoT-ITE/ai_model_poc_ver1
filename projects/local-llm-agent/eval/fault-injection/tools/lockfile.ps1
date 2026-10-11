# FI-15/16: ファイルを FileShare.None で開いて指定秒数だけ保持する（Excel で開いている状態の再現。試験仕様 §4）
param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][double]$Seconds)
$ErrorActionPreference = "Stop"
$fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
try {
  [Console]::Out.WriteLine("locked")
  [Console]::Out.Flush()
  Start-Sleep -Milliseconds ([int]($Seconds * 1000))
} finally {
  $fs.Close()
}
