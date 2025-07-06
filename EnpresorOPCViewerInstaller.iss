; Inno Setup script for Enpresor OPC Viewer
[Setup]
AppName=Enpresor OPC Viewer
AppVersion=1.0
DefaultDirName=C:\Satake\Enpresor\DataViewer
OutputDir=installer
SetupIconFile=EnpresorDataIcon.ico

[Files]
Source: "dist\EnpresorOPCDataViewBeforeRestructureLegacy\*"; DestDir: "{app}"; Flags: recursesubdirs
Source: "EnpresorDataIcon.ico"; DestDir: "{app}"
Source: "Audiowide-Regular.ttf"; DestDir: "{app}\assets"
Source: "NotoSansJP-Regular.otf"; DestDir: "{app}\assets"

[Icons]
Name: "{commondesktop}\Enpresor OPC Viewer"; Filename: "{app}\EnpresorOPCDataViewBeforeRestructureLegacy.exe"; WorkingDir: "{app}"; IconFilename: "{app}\EnpresorDataIcon.ico"
