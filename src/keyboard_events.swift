import CoreGraphics
import ApplicationServices
import Darwin
import Foundation

let keyDownMask = CGEventMask(1 << CGEventType.keyDown.rawValue)
let keyUpMask = CGEventMask(1 << CGEventType.keyUp.rawValue)
let mask = keyDownMask | keyUpMask

func emit(_ value: String) {
    FileHandle.standardOutput.write(Data((value + "\n").utf8))
}

let callback: CGEventTapCallBack = { _, type, event, _ in
    if type == .keyDown || type == .keyUp {
        let keyCode = event.getIntegerValueField(.keyboardEventKeycode)
        // macOS virtual key code 49 is the space bar.
        if keyCode == 49 {
            if type == .keyDown {
                // Ignore key-repeat events while the key is held.
                if event.getIntegerValueField(.keyboardEventAutorepeat) == 0 {
                    emit("down")
                }
            } else {
                emit("up")
            }
        }
    }
    return Unmanaged.passUnretained(event)
}

guard AXIsProcessTrusted() else {
    fputs("voice-chat: grant Accessibility permission to the keyboard listener in System Settings > Privacy & Security > Accessibility.\n", stderr)
    exit(2)
}

guard let tap = CGEvent.tapCreate(
    tap: .cgSessionEventTap,
    place: .headInsertEventTap,
    options: .listenOnly,
    eventsOfInterest: mask,
    callback: callback,
    userInfo: nil
) else {
    fputs("voice-chat: could not create the global keyboard event tap.\n", stderr)
    exit(1)
}

let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
CFRunLoopAddSource(CFRunLoopGetCurrent(), source, .commonModes)
CGEvent.tapEnable(tap: tap, enable: true)
CFRunLoopRun()
