import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Services.UPower
import qs.Commons

Item {
  id: root
  property var shell: null
  property var manifest: null
  readonly property int timeoutSeconds: 600
  readonly property string stateDirectory: Quickshell.env("HOME") + "/.local/state/omarchy/indicators"
  readonly property string stayAwakePath: stateDirectory + "/stay-awake"
  property bool stateLoaded: false
  property bool stayAwake: true
  property bool probePending: false
  property bool startupReady: false
  property bool attemptedThisIdle: false
  property int requests: 0
  property string lastRequestAt: ""
  property int lastExitCode: -1
  readonly property bool eligible: stateLoaded && !stayAwake && UPower.onBattery

  function refreshState() {
    if (stateProbe.running) probePending = true
    else stateProbe.running = true
  }

  function suspendIfIdle() {
    if (!startupReady || !eligible || !idleMonitor.isIdle || attemptedThisIdle || suspendProcess.running) return
    attemptedThisIdle = true
    requests++
    lastRequestAt = new Date().toISOString()
    console.log("battery idle suspend requested at " + lastRequestAt + " timeout=" + timeoutSeconds)
    suspendProcess.running = true
  }

  function statusJson() {
    return JSON.stringify({
      onBattery: UPower.onBattery, stayAwake: stayAwake, stateLoaded: stateLoaded,
      batteryTimeoutSeconds: timeoutSeconds, automaticSuspendOnAC: false,
      eligible: eligible, startupReady: startupReady, idle: idleMonitor.isIdle,
      requests: requests, lastRequestAt: lastRequestAt, lastExitCode: lastExitCode
    })
  }

  onEligibleChanged: {
    if (!eligible) attemptedThisIdle = false
    console.log("battery idle suspend eligible=" + eligible + " onBattery=" + UPower.onBattery + " stayAwake=" + stayAwake)
  }

  IdleMonitor {
    id: idleMonitor
    enabled: root.eligible
    timeout: root.timeoutSeconds
    respectInhibitors: true
    onIsIdleChanged: {
      console.log("battery idle monitor idle=" + isIdle)
      if (isIdle) root.suspendIfIdle()
      else root.attemptedThisIdle = false
    }
  }

  // Loading/reloading this plugin must not immediately suspend an already-idle session.
  Timer {
    interval: root.timeoutSeconds * 1000
    running: true
    repeat: false
    onTriggered: {
      root.startupReady = true
      root.suspendIfIdle()
    }
  }

  Process {
    id: stateProbe
    command: ["/usr/bin/test", "-e", root.stayAwakePath]
    onExited: function(exitCode, exitStatus) {
      root.stateLoaded = exitCode === 0 || exitCode === 1
      root.stayAwake = exitCode !== 1
      stateWatcher.reload()
      if (root.probePending) {
        root.probePending = false
        root.refreshState()
      }
    }
  }

  FileView {
    id: stateWatcher
    path: root.stateDirectory
    watchChanges: true
    printErrors: false
    onFileChanged: root.refreshState()
  }

  Process {
    id: suspendProcess
    command: ["/usr/bin/systemctl", "suspend"]
    onExited: function(exitCode, exitStatus) {
      root.lastExitCode = exitCode
      console.log("battery idle suspend request exited " + exitCode)
    }
  }

  Component.onCompleted: root.refreshState()

  ShellIpc {
    target: "sym.auto-suspend"
    function status(): string { return root.statusJson() }
  }
}
