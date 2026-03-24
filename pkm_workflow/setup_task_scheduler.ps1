# setup_task_scheduler.ps1
# 一键注册 Windows Task Scheduler 定时任务
# 使用方法: 右键 -> 以管理员身份运行此脚本
#
# 路径自动从脚本所在目录（$PSScriptRoot）推断，无需手动修改。

$TaskName    = "PKM-Daily-Fetcher"
$BatchFile   = Join-Path $PSScriptRoot "run_daily_automation.bat"
$TriggerTime = "07:00AM"   # ← 可修改为你希望的每日触发时间 (24h: "06:30AM" 等)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Write-Host ""
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host "  PKM Daily Fetcher — Task Scheduler Setup" -ForegroundColor Cyan
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host ""

# 检查 .bat 文件是否存在
if (-not (Test-Path $BatchFile)) {
    Write-Host "[错误] 找不到执行文件: $BatchFile" -ForegroundColor Red
    Write-Host "请确认脚本与 run_daily_automation.bat 位于同一目录。" -ForegroundColor Red
    exit 1
}

# 移除旧任务（如果存在）
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[更新] 检测到旧任务，正在移除..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# 创建任务动作：通过 cmd 运行 .bat 文件
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$BatchFile`""

# 触发器：每天定时运行
$Trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At $TriggerTime

# 设置：网络可用时才运行，支持睡眠唤醒，最多允许运行 30 分钟
$Settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -StartWhenAvailable   # 错过触发时间后，在电脑可用时立即补跑

# 注册任务（以当前用户身份、最高权限）
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -RunLevel Highest `
    -Force | Out-Null

# 确认结果
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host ""
    Write-Host "[成功] 定时任务已注册！" -ForegroundColor Green
    Write-Host ""
    Write-Host "  任务名称  : $TaskName"
    Write-Host "  触发时间  : 每天 $TriggerTime"
    Write-Host "  执行文件  : $BatchFile"
    Write-Host "  唤醒睡眠  : 是"
    Write-Host "  错过补跑  : 是"
    Write-Host ""
    Write-Host "可在「任务计划程序」中搜索「$TaskName」验证或手动测试运行。" -ForegroundColor Cyan
    Write-Host ""
} else {
    Write-Host "[失败] 任务注册失败，请以管理员身份重新运行此脚本。" -ForegroundColor Red
}
