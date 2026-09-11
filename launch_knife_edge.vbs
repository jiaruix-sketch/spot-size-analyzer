Option Explicit

Dim shell, fileSystem, projectRoot, appScript, userProfile
Dim pythonw, command

Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

projectRoot = fileSystem.GetParentFolderName(WScript.ScriptFullName)
appScript = fileSystem.BuildPath(projectRoot, "knife_edge_app.py")
userProfile = shell.ExpandEnvironmentStrings("%USERPROFILE%")

pythonw = fileSystem.BuildPath(userProfile, "miniconda3\pythonw.exe")
If Not fileSystem.FileExists(pythonw) Then
    pythonw = fileSystem.BuildPath(userProfile, "anaconda3\pythonw.exe")
End If

If Not fileSystem.FileExists(pythonw) Then
    MsgBox "Could not find pythonw.exe in Miniconda or Anaconda under " & userProfile, vbCritical, "Knife-Edge Analyzer"
    WScript.Quit 1
End If

If Not fileSystem.FileExists(appScript) Then
    MsgBox "Application entry point not found: " & appScript, vbCritical, "Knife-Edge Analyzer"
    WScript.Quit 1
End If

shell.CurrentDirectory = projectRoot
command = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & appScript & Chr(34)
shell.Run command, 0, False

