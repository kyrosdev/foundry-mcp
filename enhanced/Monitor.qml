import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ApplicationWindow {
    id: window
    visible: true
    width: 1136
    height: 746
    minimumWidth: 790
    minimumHeight: 550
    title: "Foundry MCP | Monitor"
    color: "#17171a"
    font.family: "Segoe UI"
    property var snapshot: monitorModel.state
    property bool overview: startView === "graph"
    property bool motion: allowMotion
    property bool reducedMotion: !motion
    property color ink: "#f2efed"
    property color muted: "#aaa4a0"
    property color accent: snapshot.accent || "#f3f2f0"
    property color surface: "#101011"
    property string eventDetail: ""
    property double clock: Date.now()
    onSnapshotChanged: if (overview) graph.requestPaint()
    onMotionChanged: graph.requestPaint()
    onOverviewChanged: {
        if (motion) pageFade.restart()
    }
    NumberAnimation {
        id: pageFade
        target: window.overview ? flowPage : historyPage
        property: "opacity"
        from: 0.65; to: 1; duration: 180
        easing.type: Easing.OutCubic
        onStopped: { flowPage.opacity = 1; historyPage.opacity = 1 }
    }
    Shortcut { sequence: "Ctrl+1"; onActivated: window.overview = false }
    Shortcut { sequence: "Ctrl+2"; onActivated: window.overview = true }
    Shortcut { sequence: "Escape"; onActivated: monitorModel.select("") }
    Timer {
        interval: 85
        repeat: true
        running: window.visible && window.overview && window.motion && (window.snapshot.running || 0) > 0
        onTriggered: { window.clock = Date.now(); graph.requestPaint() }
    }
    Timer {
        interval: 1000
        repeat: true
        running: window.visible && (window.snapshot.running || 0) > 0
        onTriggered: window.clock = Date.now()
    }

    component Caption: Text {
        color: window.muted
        font.pixelSize: 12
        textFormat: Text.PlainText
        elide: Text.ElideRight
    }
    component Heading: Text {
        color: window.ink
        font.pixelSize: 16
        font.weight: Font.DemiBold
        textFormat: Text.PlainText
    }
    component Sheet: Rectangle {
        radius: 15
        color: window.surface
        border.color: "#383331"
        border.width: 1
    }
    component Provider: Rectangle {
        property string name: ""
        property color hue: window.accent
        implicitWidth: providerText.implicitWidth+32
        implicitHeight: 28
        radius: 14
        color: "#201d1c"
        border.color: "#393432"
        Rectangle { x: 10; anchors.verticalCenter: parent.verticalCenter; width: 6; height: 6; radius: 3; color: parent.hue }
        Caption { id: providerText; x: 21; anchors.verticalCenter: parent.verticalCenter; text: parent.name; color: parent.hue }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: 16
        radius: 22
        border.color: "#313135"
        border.width: 1
        gradient: Gradient {
            GradientStop { position: 0; color: "#1d1d20" }
            GradientStop { position: .52; color: "#151517" }
            GradientStop { position: 1; color: "#141416" }
        }

        RowLayout {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 13

            Sheet {
                Layout.minimumWidth: 0
                Layout.preferredWidth: window.width < 960 ? 156 : 185
                Layout.fillHeight: true
                color: "#0e0d0d"
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 13
                    spacing: 8
                    RowLayout {
                        Layout.topMargin: 4
                        spacing: 9
                        Rectangle { width: 17; height: 17; radius: 5; color: "transparent"; border.color: window.accent; border.width: 2; rotation: 13 }
                        Heading { text: "Foundry MCP"; font.pixelSize: 14; Layout.fillWidth: true }
                    }
                    Item { Layout.preferredHeight: 8 }
                    MonitorButton { Layout.fillWidth: true; glyph: "flow"; text: "Overview"; selected: window.overview; ink: window.accent; onClicked: window.overview = true }
                    MonitorButton { Layout.fillWidth: true; glyph: "activity"; text: "Activity"; selected: !window.overview; quiet: true; onClicked: window.overview = false }
                    Caption { text: "Agents"; Layout.topMargin: 13 }
                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 4
                        model: [{id:"",name:"All agents",color:window.accent,main:true,online:true,action:""}].concat(window.snapshot.agents || [])
                        ScrollBar.vertical: ScrollBar { width: 5 }
                        delegate: ItemDelegate {
                            id: person
                            required property var modelData
                            width: ListView.view.width
                            height: modelData.id ? 50 : 35
                            padding: 8
                            onClicked: monitorModel.select(modelData.id)
                            background: Rectangle {
                                radius: 8
                                color: (window.snapshot.selected.id || "") === person.modelData.id ? "#282322" : person.hovered ? "#1c1919" : "transparent"
                                border.width: person.visualFocus ? 1 : 0
                                border.color: person.modelData.color
                            }
                            contentItem: Column {
                                spacing: 3
                                Caption {
                                    width: parent.width
                                    text: (person.modelData.main ? "" : "    ") + person.modelData.name
                                    color: person.modelData.online ? person.modelData.color : window.muted
                                    font.pixelSize: 13
                                }
                                Caption {
                                    width: parent.width
                                    visible: person.modelData.id !== ""
                                    text: (person.modelData.main ? "" : "    ") + (person.modelData.action ||
                                          (!person.modelData.online ? "Disconnected" : person.modelData.main ? "Lead agent" : "Subagent"))
                                }
                            }
                        }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: "#2e2927" }
                    Caption { Layout.topMargin: 4; text: window.snapshot.rooms.length ? "Team active" : "Solo mode" }
                    MonitorButton { Layout.fillWidth: true; quiet: true; glyph: "motion"; text: "Animations"; selected: window.motion; Accessible.description: window.motion ? "On" : "Reduced"; onClicked: window.motion = !window.motion }
                    MonitorButton { Layout.fillWidth: true; quiet: true; text: window.snapshot.cursorsVisible === false ? "Show cursors" : "Hide cursors"; onClicked: monitorModel.toggleCursors() }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 11

                RowLayout {
                    Layout.topMargin: 3
                    Layout.bottomMargin: 2
                    ColumnLayout {
                        spacing: 3
                        Heading { text: window.overview ? "Monitor / Flow" : "Monitor / Activity"; font.pixelSize: 16 }
                    }
                    Item { Layout.fillWidth: true }
                    Repeater {
                        model: window.snapshot.clients || []
                        Provider { required property var modelData; name: modelData.name; hue: modelData.color }
                    }
                    Caption { visible: !window.snapshot.online; text: "Waiting for connection"; color: "#e1d4cd" }
                }

                RowLayout {
                    visible: window.overview
                    Layout.fillWidth: true
                    spacing: 7
                    MonitorButton { text: "Flow"; selected: true; glyph: "flow"; enabled: false }
                    MonitorButton { text: "History"; glyph: "activity"; onClicked: window.overview = false }
                    Item { Layout.fillWidth: true }
                    Caption { text: window.snapshot.count + " agents  /  " + window.snapshot.running + " running" }
                }

                RowLayout {
                    id: flowPage
                    visible: window.overview
                    Layout.minimumWidth: 0
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 11

                    Sheet {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 13
                            spacing: 6
                            RowLayout {
                                Layout.fillWidth: true
                                Heading { text: "Agent flow"; Layout.fillWidth: true }
                                Caption { text: window.snapshot.rooms.length ? "Team active" : "Solo" }
                                MonitorButton {
                                    text: "Reset view"
                                    glyph: "reset"
                                    implicitHeight: 30
                                    onClicked: { graph.zoom = 1; graph.panX = 0; graph.panY = 0; graph.requestPaint() }
                                }
                            }
                            Canvas {
                                id: graph
                                objectName: "agentGraph"
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                property var nodes: []
                                property var ribbons: []
                                property string hoveredNode: ""
                                property real zoom: 1
                                property real panX: 0
                                property real panY: 0
                                property real pressX: 0
                                property real pressY: 0
                                property int cursorIndex: -1
                                activeFocusOnTab: true
                                Accessible.name: "Agent flow"
                                onWidthChanged: requestPaint()
                                onHeightChanged: requestPaint()
                                onVisibleChanged: if (visible) requestPaint()
                                Keys.onLeftPressed: step(-1)
                                Keys.onRightPressed: step(1)
                                function step(amount) {
                                    if (!nodes.length) return
                                    cursorIndex = (cursorIndex + amount + nodes.length) % nodes.length
                                    monitorModel.select(nodes[cursorIndex].id)
                                }
                                onPaint: {
                                    const ctx = getContext("2d")
                                    ctx.reset()
                                    const groups = window.snapshot.groups.slice(0,4)
                                    if (!groups.length || width < 70 || height < 55) {
                                        graph.nodes = []
                                        return
                                    }
                                    const selected = window.snapshot.selected.id
                                    const rooms = window.snapshot.rooms
                                    const common = rooms.find(room => groups.every(group =>
                                        group.slice(0,7).every(agent => room.members.includes(agent.id))))
                                    const top = common ? 34 : 10
                                    const lane = (height-top-8)/groups.length
                                    const sourceX = Math.min(146,width*.29)
                                    const targetX = Math.max(sourceX+35,width-Math.min(136,width*.32))
                                    let nodes = [], ribbons = []
                                    ctx.translate(width/2+panX,height/2+panY)
                                    ctx.scale(zoom,zoom)
                                    ctx.translate(-width/2,-height/2)
                                    function rounded(x,y,w,h,r,fill,stroke) {
                                        if(w<=0 || h<=0) return
                                        r = Math.min(r,w/2,h/2)
                                        ctx.beginPath()
                                        ctx.moveTo(x+r,y);ctx.lineTo(x+w-r,y);ctx.quadraticCurveTo(x+w,y,x+w,y+r)
                                        ctx.lineTo(x+w,y+h-r);ctx.quadraticCurveTo(x+w,y+h,x+w-r,y+h)
                                        ctx.lineTo(x+r,y+h);ctx.quadraticCurveTo(x,y+h,x,y+h-r)
                                        ctx.lineTo(x,y+r);ctx.quadraticCurveTo(x,y,x+r,y);ctx.closePath()
                                        ctx.fillStyle=fill;ctx.fill()
                                        if(stroke){ctx.strokeStyle=stroke;ctx.stroke()}
                                    }
                                    if(common) {
                                        rounded(4,4,width-8,height-8,11,"#161412","#332b27")
                                        ctx.fillStyle="#bcafa8";ctx.font="11px Segoe UI"
                                        ctx.fillText(common.name,15,21,width-30)
                                    }
                                    const highlight = hoveredNode || selected
                                    for(let i=0;i<groups.length;i++) {
                                        const group=groups[i], root=group[0], children=group.slice(1,7)
                                        const y0=top+i*lane, cy=y0+lane/2
                                        const step=Math.min(44,(lane-12)/Math.max(1,children.length))
                                        const bar=Math.max(5,Math.min(22,step*.58))
                                        const hue=root.online ? (window.snapshot.clients.length > 1 ? "#e9e4df" : root.color) : "#716a65"
                                        const labelWidth=sourceX-17
                                        ctx.font="12px Segoe UI"; ctx.fillStyle=hue
                                        ctx.textAlign="left";ctx.textBaseline="middle"
                                        ctx.fillText(root.name,12,cy,labelWidth)
                                        const rootSize=Math.max(17,children.length*(bar+2))
                                        rounded(sourceX,cy-rootSize/2,5,rootSize,2,hue,highlight===root.id?"#ffffff":null)
                                        nodes.push({id:root.id,x:sourceX,y:cy,left:8,right:sourceX+10,half:Math.max(13,rootSize/2)})
                                        if(i>0) {
                                            ctx.fillStyle="#2c2622"; ctx.fillRect(14,y0,width-28,1)
                                        }
                                        children.forEach((agent,j)=>{
                                            const sy=cy+(j-(children.length-1)/2)*(bar+2)
                                            const ty=cy+(j-(children.length-1)/2)*step
                                            const hot=highlight===agent.id || highlight===root.id
                                            const strength=hot?.94:agent.action?.83:.50
                                            const start=sourceX+7,end=targetX-3,mid=(start+end)/2
                                            const grad=ctx.createLinearGradient(start,0,end,0)
                                            const mixed = window.snapshot.clients.length > 1
                                            const endpoint = mixed ? "#ff9d6d" : hue
                                            grad.addColorStop(0,Qt.rgba(0.98,0.96,0.94,0.20))
                                            grad.addColorStop(.29,mixed ? "#f3efeb" : hue)
                                            grad.addColorStop(.65,mixed ? "#efc9b2" : hue)
                                            grad.addColorStop(1,endpoint)
                                            ctx.globalAlpha=strength+(agent.action&&window.motion?.045*Math.sin(window.clock/300):0)
                                            ctx.fillStyle=grad;ctx.beginPath()
                                            ctx.moveTo(start,sy-bar/2)
                                            ctx.bezierCurveTo(mid,sy-bar/2,mid,ty-bar/2,end,ty-bar/2)
                                            ctx.lineTo(end,ty+bar/2)
                                            ctx.bezierCurveTo(mid,ty+bar/2,mid,sy+bar/2,start,sy+bar/2)
                                            ctx.closePath();ctx.fill();ctx.globalAlpha=1
                                            rounded(targetX,ty-bar/2,5,bar,2,agent.action||hot?endpoint:"#766b65",hot?endpoint:null)
                                            ctx.font="11px Segoe UI";ctx.fillStyle=hot||agent.action?hue:"#b9ada7"
                                            ctx.fillText(agent.name,targetX+11,ty,width-targetX-20)
                                            if(agent.action && window.motion) {
                                                const t=(window.clock/2600+j*.22)%1
                                                const px=start+(end-start)*(3*(1-t)*(1-t)*t*.5+3*(1-t)*t*t*.5+t*t*t)
                                                const py=sy+(ty-sy)*t*t*(3-2*t)
                                                ctx.globalAlpha=.30*Math.sin(Math.PI*t)
                                                ctx.fillStyle="#fff5ea";ctx.beginPath();ctx.arc(px,py,2,0,Math.PI*2);ctx.fill()
                                                ctx.globalAlpha=1
                                            }
                                            nodes.push({id:agent.id,x:targetX,y:ty,left:targetX-7,right:width-7,half:Math.max(4,step/2)})
                                            ribbons.push({id:agent.id,start:start,end:end,sy:sy,ty:ty,half:bar/2})
                                        })
                                    }
                                    const sx = x => (x-width/2)*zoom+width/2+panX
                                    const sy = y => (y-height/2)*zoom+height/2+panY
                                    graph.nodes=nodes.map(n=>({id:n.id,x:sx(n.x),y:sy(n.y),
                                                               left:sx(n.left),right:sx(n.right),half:n.half*zoom}))
                                    graph.ribbons=ribbons
                                }
                                function hit(x,y) {
                                    const node = nodes.find(n=>x>=n.left && x<=n.right && Math.abs(n.y-y)<=n.half)
                                    if (node) return node
                                    const px = (x-width/2-panX)/zoom+width/2
                                    const py = (y-height/2-panY)/zoom+height/2
                                    return ribbons.find(r => {
                                        if (px < r.start || px > r.end) return false
                                        const fraction=(px-r.start)/(r.end-r.start)
                                        let lo=0, hi=1
                                        for (let i=0;i<9;i++) {
                                            const t=(lo+hi)/2
                                            const curve=1.5*t-1.5*t*t+t*t*t
                                            if(curve<fraction) lo=t; else hi=t
                                        }
                                        const t=(lo+hi)/2
                                        return Math.abs(py-r.sy-(r.ty-r.sy)*t*t*(3-2*t))<=r.half
                                    })
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                                    hoverEnabled: true
                                    cursorShape: !!graph.hit(mouseX,mouseY)?Qt.PointingHandCursor:Qt.ArrowCursor
                                    onPressed: function(e) { graph.pressX=e.x; graph.pressY=e.y }
                                    onExited: { graph.hoveredNode = ""; graph.requestPaint() }
                                    onPositionChanged: function(e) {
                                        if (pressedButtons & Qt.RightButton) {
                                            graph.panX += e.x-graph.pressX; graph.panY += e.y-graph.pressY
                                            graph.pressX=e.x;graph.pressY=e.y;graph.requestPaint()
                                        } else {
                                            const hit = graph.hit(e.x,e.y)
                                            const next = hit ? hit.id : ""
                                            if (next !== graph.hoveredNode) { graph.hoveredNode = next; graph.requestPaint() }
                                        }
                                    }
                                    onClicked: function(e) {
                                        if(e.button!==Qt.LeftButton) return
                                        const n=graph.hit(e.x,e.y)
                                        if(n){graph.forceActiveFocus();monitorModel.select(n.id)}
                                    }
                                    onWheel: function(e) {
                                        if(e.modifiers & Qt.ControlModifier) {
                                            graph.zoom=Math.max(.65,Math.min(2.2,graph.zoom*(e.angleDelta.y>0?1.14:1/1.14)))
                                            graph.requestPaint()
                                        } else e.accepted=false
                                    }
                                }
                                Caption { anchors.centerIn: parent; visible: !window.snapshot.groups.length; text: "Waiting for agents to connect" }
                            }
                            Caption { Layout.fillWidth: true; text: "Highlighted paths show active calls." }
                            Caption {
                                Layout.fillWidth: true
                                visible: window.snapshot.groups.length > 4 || window.snapshot.groups.some(g => g.length > 7)
                                text: "Agents outside this view are listed in the sidebar."
                            }
                        }
                    }

                    Sheet {
                        Layout.minimumWidth: 0
                        Layout.preferredWidth: window.width < 960 ? 151 : 228
                        Layout.fillHeight: true
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 8
                            Heading { text: "Inspector" }
                            Rectangle { Layout.fillWidth: true; height: 1; color: "#28282b" }
                            Heading {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                font.pixelSize: 14
                                text: window.snapshot.selected.name || "Active agents"
                                color: window.snapshot.selected.color || window.ink
                            }
                            Caption {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                visible: !!window.snapshot.selected.id
                                text: window.snapshot.selected.action || (window.snapshot.selected.online ? "Idle" : "Disconnected")
                            }
                            Caption {
                                Layout.fillWidth: true
                                visible: !!window.snapshot.selected.started
                                text: Math.max(0, (window.clock/1000 - (window.snapshot.selected.started || 0))).toFixed(0) + " s"
                            }
                            Caption { Layout.fillWidth: true; text: window.snapshot.selected.place || ""; visible: !!text }
                            ListView {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                clip: true
                                spacing: 5
                                model: window.snapshot.selected.id ? window.snapshot.tasks : window.snapshot.agents.filter(a=>a.action)
                                delegate: ItemDelegate {
                                    id: focusRow
                                    required property var modelData
                                    width: ListView.view.width
                                    height: 53
                                    padding: 5
                                    onClicked: if (modelData.id) monitorModel.select(modelData.id)
                                    background: Rectangle {
                                        radius: 6
                                        color: (focusRow.hovered || focusRow.visualFocus) && focusRow.modelData.id ? "#242326" : "transparent"
                                        Behavior on color { enabled: window.motion; ColorAnimation { duration: 125 } }
                                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: "#242426" }
                                    }
                                    contentItem: Column {
                                        Caption { width: parent.width; color: focusRow.modelData.color || window.ink; text: focusRow.modelData.name || focusRow.modelData.title }
                                        Caption { width: parent.width; text: focusRow.modelData.action || focusRow.modelData.status }
                                        Caption { width: parent.width; visible: !!(focusRow.modelData.paths || []).length; text: (focusRow.modelData.paths || []).join(" / ") }
                                    }
                                }
                                Caption { anchors.top: parent.top; width: parent.width; wrapMode: Text.Wrap; visible: parent.count===0 && !window.snapshot.selected.id; text: "No calls in progress." }
                            }
                        }
                    }
                }

                Sheet {
                    id: historyPage
                    visible: !window.overview
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredHeight: window.overview ? (window.height < 620 ? 100 : 134) : 260
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 13
                        spacing: 5
                        RowLayout {
                            Heading { text: "Recent activity"; Layout.fillWidth: true }
                            Caption { text: window.snapshot.selected.name || "All agents" }
                        }
                        ListView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            model: window.snapshot.history || []
                            ScrollBar.vertical: ScrollBar { width: 5 }
                            delegate: ItemDelegate {
                                id: eventRow
                                required property var modelData
                                width: ListView.view.width
                                height: 32
                                padding: 5
                                onClicked: window.eventDetail = [modelData.actor,modelData.place,modelData.duration].filter(Boolean).join(" / ")
                                background: Rectangle { radius: 6; color: eventRow.hovered || eventRow.visualFocus ? "#241f1d" : "transparent" }
                                contentItem: RowLayout {
                                    spacing: 12
                                    Caption { text: eventRow.modelData.time; Layout.preferredWidth: 61 }
                                    Caption { text: eventRow.modelData.name; color: window.ink; Layout.fillWidth: true }
                                    Caption { text: eventRow.modelData.actor; color: eventRow.modelData.color; Layout.preferredWidth: 108 }
                                    Caption { text: eventRow.modelData.ok ? eventRow.modelData.duration : "Failed"; color: eventRow.modelData.ok ? window.muted : "#ffafa9"; Layout.preferredWidth: 64; horizontalAlignment: Text.AlignRight }
                                }
                            }
                            Caption { anchors.centerIn: parent; visible: parent.count === 0; text: "Completed calls appear here." }
                        }
                        Caption { visible: !!window.eventDetail; Layout.fillWidth: true; text: window.eventDetail }
                    }
                }
            }
        }
    }
}
