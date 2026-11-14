import QtQuick
import QtQuick.Controls.Basic

Button {
    id: control
    property bool selected: false
    property color ink: "#eeeeee"
    property bool quiet: false
    property string glyph: ""
    implicitHeight: 35
    leftPadding: glyph ? 33 : 13
    rightPadding: 13
    font.pixelSize: 13
    contentItem: Text {
        text: control.text
        color: control.selected ? control.ink : "#c5c1bf"
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    Canvas {
        z: 1
        x: 11; anchors.verticalCenter: parent.verticalCenter
        width: 15; height: 15
        visible: !!control.glyph
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            ctx.strokeStyle = "#bbb2ad"
            ctx.lineWidth = 1.25
            ctx.lineCap = "round"
            ctx.lineJoin = "round"
            ctx.beginPath()
            if (control.glyph === "flow") {
                ctx.moveTo(2,7.5);ctx.bezierCurveTo(8,7.5,8,3,13,3)
                ctx.moveTo(2,7.5);ctx.bezierCurveTo(8,7.5,8,12,13,12)
                ctx.moveTo(2,5);ctx.lineTo(2,10)
            } else if (control.glyph === "activity") {
                ctx.moveTo(2,8);ctx.lineTo(5,8);ctx.lineTo(7,4);ctx.lineTo(9,11);ctx.lineTo(11,8);ctx.lineTo(13,8)
            } else if (control.glyph === "reset") {
                ctx.arc(7.5,7.5,4.5,-.9,4.2)
                ctx.moveTo(3,3);ctx.lineTo(3,6);ctx.lineTo(6,6)
            } else {
                ctx.moveTo(5,3);ctx.lineTo(11,7.5);ctx.lineTo(5,12);ctx.closePath()
            }
            ctx.stroke()
        }
    }
    background: Rectangle {
        radius: 9
        color: control.selected ? "#282523" : control.hovered ? "#201e1e" : control.quiet ? "transparent" : "#161515"
        border.width: control.visualFocus || !control.quiet ? 1 : 0
        border.color: control.visualFocus ? control.ink : "#302c2a"
        Behavior on color {
            enabled: control.Window.window && !control.Window.window.reducedMotion
            ColorAnimation { duration: 110 }
        }
    }
}
