#define MyAppName "SA-LogiFlow Local Scan Agent"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "SA-LogiFlow"
#define MyAppExeName "SA-LogiFlow-Agent.exe"

[Setup]
AppId={{C2D2FA46-2B0D-4DCA-9B0A-8B2732A1A0E3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\SA-LogiFlow
DefaultGroupName=SA-LogiFlow
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=SA-LogiFlow-Agent-Windows
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}

[Files]
Source: "..\..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Run]
Filename: "{sys}\schtasks.exe"; Parameters: "/Create /TN ""SA-LogiFlow Agent"" /TR ""{app}\{#MyAppExeName}"" /SC ONLOGON /RL LIMITED /F"; Flags: runhidden
Filename: "{sys}\schtasks.exe"; Parameters: "/Run /TN ""SA-LogiFlow Agent"""; Flags: runhidden

[UninstallRun]
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""SA-LogiFlow Agent"" /F"; Flags: runhidden
