; Inno Setup script for the standalone command-line build.
;   ISCC.exe /DAppVersion=0.1.2 packaging\windows\installer.iss   (from openalex_corpus\)
; Installs for the current user by default and adds the folder to the user PATH,
; so `openalex-corpus` works in a new terminal. `openalex-corpus self-update`
; later re-runs a newer setup silently.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define Root SourcePath + "..\.."

[Setup]
AppId={{C2B4E7A1-3F59-4D1E-8A6B-7E0F9C2D5B48}
AppName=openalex-corpus
AppVersion={#AppVersion}
AppPublisher=Jongsu Lim
AppPublisherURL=https://github.com/jongsu-king/openalex-corpus-harvester
AppUpdatesURL=https://github.com/jongsu-king/openalex-corpus-harvester/releases
DefaultDirName={autopf}\openalex-corpus
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Root}\out
OutputBaseFilename=openalex-corpus-{#AppVersion}-windows-x64-setup
LicenseFile={#Root}\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes
UninstallDisplayName=openalex-corpus {#AppVersion}

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#Root}\dist\openalex-corpus\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Root}\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Registry]
Root: HKA; Subkey: "{code:EnvKey}"; ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; Check: NeedsAddPath(ExpandConstant('{app}'))

[Code]
function EnvKey(Param: String): String;
begin
  if IsAdminInstallMode then
    Result := 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
  else
    Result := 'Environment';
end;

function RootKey(): Integer;
begin
  if IsAdminInstallMode then Result := HKEY_LOCAL_MACHINE else Result := HKEY_CURRENT_USER;
end;

function NeedsAddPath(Dir: String): Boolean;
var
  Paths: String;
begin
  if not RegQueryStringValue(RootKey(), EnvKey(''), 'Path', Paths) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') = 0;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Paths, Dir: String;
  P: Integer;
begin
  if CurUninstallStep <> usPostUninstall then exit;
  if not RegQueryStringValue(RootKey(), EnvKey(''), 'Path', Paths) then exit;
  Dir := ExpandConstant('{app}');
  P := Pos(';' + Uppercase(Dir), Uppercase(Paths));
  if P > 0 then
  begin
    Delete(Paths, P, Length(Dir) + 1);
    RegWriteExpandStringValue(RootKey(), EnvKey(''), 'Path', Paths);
  end;
end;
