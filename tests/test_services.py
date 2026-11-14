"""UPnP 协议层离线冒烟测试（不依赖 mpv / GUI / 网络）。

用法: python tests/test_services.py
"""

import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from upnp.gena import GenaManager  # noqa: E402
from upnp.http_server import DeviceContext  # noqa: E402
from upnp.services import ServiceRegistry  # noqa: E402
from upnp.state import RendererState  # noqa: E402

AVT = "urn:schemas-upnp-org:service:AVTransport:1"
RC = "urn:schemas-upnp-org:service:RenderingControl:1"
CM = "urn:schemas-upnp-org:service:ConnectionManager:1"

SOAP = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
        '<u:{a} xmlns:u="{t}">{args}</u:{a}></s:Body></s:Envelope>')


class FakeController:
    def __init__(self, state):
        self.calls = []
        self.state = state

    def load(self, url, metadata="", title=""):
        self.calls.append(("load", url, title))

    def play(self):
        self.calls.append(("play",))

    def pause(self):
        self.calls.append(("pause",))

    def stop(self):
        self.calls.append(("stop",))

    def seek_absolute(self, sec):
        self.calls.append(("seek", sec))

    def set_volume(self, v):
        self.state.set_volume(v)


def main() -> int:
    st = RendererState()
    gena = GenaManager(st)
    ctl = FakeController(st)
    reg = ServiceRegistry(st, ctl, gena)

    # 1. SetAVTransportURI（含 DIDL 二次转义与标题解析）
    didl = ('<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<item><dc:title>测试视频ABC</dc:title></item></DIDL-Lite>')
    body = SOAP.format(a="SetAVTransportURI", t=AVT,
                       args="<InstanceID>0</InstanceID><CurrentURI>http://x/a.mp4</CurrentURI>"
                            f"<CurrentURIMetaData>{escape(didl)}</CurrentURIMetaData>")
    resp = reg.dispatch(f"{AVT}#SetAVTransportURI", body.encode())
    assert "SetAVTransportURIResponse" in resp, resp
    assert ctl.calls[0] == ("load", "http://x/a.mp4", "测试视频ABC"), ctl.calls[0]
    print("1. SetAVTransportURI + DIDL 标题解析 OK")

    # 2. Play
    reg.dispatch(f"{AVT}#Play", SOAP.format(a="Play", t=AVT,
                 args="<InstanceID>0</InstanceID><Speed>1</Speed>").encode())
    assert ctl.calls[-1] == ("play",)
    print("2. Play OK")

    # 3. GetPositionInfo 时间格式
    st.set_duration(120.0)
    st.set_position(15.5)
    resp = reg.dispatch(f"{AVT}#GetPositionInfo", SOAP.format(a="GetPositionInfo", t=AVT,
                        args="<InstanceID>0</InstanceID>").encode())
    assert "<RelTime>0:00:15.500</RelTime>" in resp, resp
    assert "<TrackDuration>0:02:00.000</TrackDuration>" in resp, resp
    print("3. GetPositionInfo OK (H:MM:SS.mmm)")

    # 4. Seek REL_TIME
    reg.dispatch(f"{AVT}#Seek", SOAP.format(a="Seek", t=AVT,
                 args="<InstanceID>0</InstanceID><Unit>REL_TIME</Unit><Target>0:01:30</Target>").encode())
    assert ctl.calls[-1] == ("seek", 90.0), ctl.calls[-1]
    print("4. Seek REL_TIME -> 90s OK")

    # 5. RenderingControl 音量
    reg.dispatch(f"{RC}#SetVolume", SOAP.format(a="SetVolume", t=RC,
                 args="<InstanceID>0</InstanceID><Channel>Master</Channel><DesiredVolume>42</DesiredVolume>").encode())
    assert st.get_volume() == 42
    resp = reg.dispatch(f"{RC}#GetVolume", SOAP.format(a="GetVolume", t=RC,
                        args="<InstanceID>0</InstanceID><Channel>Master</Channel>").encode())
    assert "<CurrentVolume>42</CurrentVolume>" in resp
    print("5. RenderingControl SetVolume/GetVolume OK")

    # 6. ConnectionManager 协议列表
    resp = reg.dispatch(f"{CM}#GetProtocolInfo", SOAP.format(a="GetProtocolInfo", t=CM, args="").encode())
    assert "video/mp4" in resp and "mpegurl" in resp
    print("6. ConnectionManager GetProtocolInfo OK")

    # 7. desc.xml 生成
    dev = DeviceContext("192.168.1.100", 9090, "test-uuid-123", "客厅电视")
    d = dev.desc_xml().decode()
    assert "MediaRenderer" in d and "test-uuid-123" in d and "客厅电视" in d
    assert "/ctl/AVTransport" in d and "/evt/RenderingControl" in d
    assert dev.location_url() == "http://192.168.1.100:9090/desc.xml"
    print("7. desc.xml 生成 OK")

    # 8. GENA LastChange XML
    st.set_transport_state("PLAYING")
    xml = gena.avt_lastchange()
    assert 'TransportState val="PLAYING"' in xml and "metadata-1-0/AVT/" in xml
    xml2 = gena.rc_lastchange()
    assert 'Volume channel="Master" val="42"' in xml2
    ps = gena._propertyset(xml)
    assert "LastChange" in ps and "&lt;Event" in ps
    print("8. GENA LastChange / propertyset OK")

    # 9. 未知 action -> fault
    try:
        reg.dispatch(f"{AVT}#NotExist", SOAP.format(a="NotExist", t=AVT, args="").encode())
        print("9. FAIL: 未知 action 未抛错")
        return 1
    except Exception:
        fault = ServiceRegistry.fault_xml(401, "Invalid action NotExist")
        assert "UPnPError" in fault and "401" in fault
        print("9. 未知 action -> UPnP fault OK")

    # 10. SCPD 文件存在
    res = Path(__file__).resolve().parent.parent / "upnp" / "resources"
    for name in ("desc.xml", "AVTransport.xml", "ConnectionManager.xml", "RenderingControl.xml"):
        assert (res / name).exists(), f"缺少 {name}"
    print("10. SCPD/desc 资源文件齐全")

    print("=== 协议层冒烟测试全部通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
