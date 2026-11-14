"""UPnP SOAP 控制端点：AVTransport / ConnectionManager / RenderingControl。

按 SOAPACTION 头 `serviceType#Action` 分发，返回标准 SOAP 响应信封。
所有 handler 只依赖 RendererState（读）与 MpvController（写）。
"""

from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from upnp.state import RendererState, upnp_time, parse_upnp_time

SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
AVT_TYPE = "urn:schemas-upnp-org:service:AVTransport:1"
CM_TYPE = "urn:schemas-upnp-org:service:ConnectionManager:1"
RC_TYPE = "urn:schemas-upnp-org:service:RenderingControl:1"

SINK_PROTOCOL_INFO = ",".join([
    "http-get:*:video/mp4:*",
    "http-get:*:video/x-matroska:*",
    "http-get:*:video/webm:*",
    "http-get:*:video/x-flv:*",
    "http-get:*:video/mpeg:*",
    "http-get:*:video/x-msvideo:*",
    "http-get:*:video/avi:*",
    "http-get:*:video/quicktime:*",
    "http-get:*:video/3gpp:*",
    "http-get:*:video/x-ms-wmv:*",
    "http-get:*:video/h264:*",
    "http-get:*:application/vnd.apple.mpegurl:*",
    "http-get:*:application/x-mpegurl:*",
    "http-get:*:application/dash+xml:*",
    "http-get:*:audio/mpeg:*",
    "http-get:*:audio/mp4:*",
    "http-get:*:audio/aac:*",
    "http-get:*:audio/x-flac:*",
    "http-get:*:audio/wav:*",
    "http-get:*:audio/x-wav:*",
    "http-get:*:image/jpeg:*",
    "http-get:*:image/png:*",
    "http-get:*:*:*",
])

DC_NS = "http://purl.org/dc/elements/1.1/"


class UpnpActionError(Exception):
    def __init__(self, code: int, desc: str) -> None:
        super().__init__(desc)
        self.code = code
        self.desc = desc


def extract_title(metadata: str) -> str:
    """从 DIDL-Lite 元数据（已被 SOAP 解包一层转义）中提取 dc:title。"""
    if not metadata or "<" not in metadata:
        return ""
    try:
        root = ET.fromstring(metadata)
    except ET.ParseError:
        return ""
    el = root.find(f".//{{{DC_NS}}}title")
    return (el.text or "").strip() if el is not None else ""


class ServiceRegistry:
    def __init__(self, state: RendererState, controller, gena) -> None:
        self.state = state
        self.controller = controller
        self.gena = gena
        self._handlers = {
            (AVT_TYPE, "SetAVTransportURI"): self._avt_set_uri,
            (AVT_TYPE, "Play"): self._avt_play,
            (AVT_TYPE, "Pause"): self._avt_pause,
            (AVT_TYPE, "Stop"): self._avt_stop,
            (AVT_TYPE, "Seek"): self._avt_seek,
            (AVT_TYPE, "GetTransportInfo"): self._avt_get_transport_info,
            (AVT_TYPE, "GetPositionInfo"): self._avt_get_position_info,
            (AVT_TYPE, "GetMediaInfo"): self._avt_get_media_info,
            (AVT_TYPE, "GetDeviceCapabilities"): self._avt_get_capabilities,
            (AVT_TYPE, "GetTransportSettings"): self._avt_get_settings,
            (AVT_TYPE, "GetCurrentTransportActions"): self._avt_current_actions,
            (CM_TYPE, "GetProtocolInfo"): self._cm_get_protocol_info,
            (CM_TYPE, "GetCurrentConnectionIDs"): self._cm_get_connection_ids,
            (CM_TYPE, "GetCurrentConnectionInfo"): self._cm_get_connection_info,
            (RC_TYPE, "GetVolume"): self._rc_get_volume,
            (RC_TYPE, "SetVolume"): self._rc_set_volume,
            (RC_TYPE, "GetMute"): self._rc_get_mute,
            (RC_TYPE, "SetMute"): self._rc_set_mute,
            (RC_TYPE, "ListPresets"): self._rc_list_presets,
            (RC_TYPE, "SelectPreset"): self._rc_select_preset,
        }

    # ---------- 分发入口 ----------
    def dispatch(self, soapaction: str, body: bytes) -> str:
        """执行 action，返回响应 XML 字符串；失败抛 UpnpActionError。"""
        service = ""
        action = ""
        if soapaction:
            soapaction = soapaction.strip().strip('"')
            if "#" in soapaction:
                service, action = soapaction.split("#", 1)
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            raise UpnpActionError(402, "Invalid XML")
        body_el = root.find(f"{{{SOAP_ENV}}}Body")
        if body_el is None or len(body_el) == 0:
            raise UpnpActionError(402, "Missing SOAP body")
        action_el = list(body_el)[0]
        if not action:
            action = action_el.tag.split("}")[-1]
        handler = self._handlers.get((service, action))
        if handler is None:
            raise UpnpActionError(401, f"Invalid action {action}")
        args = {}
        for child in action_el:
            args[child.tag.split("}")[-1]] = child.text or ""
        out = handler(args)
        return self._envelope(service, action, out)

    @staticmethod
    def _envelope(service: str, action: str, args: dict) -> str:
        inner = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in args.items())
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<s:Envelope xmlns:s="{SOAP_ENV}" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f"<s:Body><u:{action}Response xmlns:u=\"{service}\">{inner}</u:{action}Response></s:Body>"
            "</s:Envelope>"
        )

    @staticmethod
    def fault_xml(code: int, desc: str) -> str:
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<s:Envelope xmlns:s="{SOAP_ENV}" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            "<s:Body><s:Fault>"
            "<faultcode>s:Client</faultcode>"
            "<faultstring>UPnPError</faultstring>"
            "<detail><UPnPError xmlns=\"urn:schemas-upnp-org:control-1-0\">"
            f"<errorCode>{code}</errorCode><errorDescription>{escape(desc)}</errorDescription>"
            "</UPnPError></detail>"
            "</s:Fault></s:Body></s:Envelope>"
        )

    def _instance_id(self, args: dict) -> None:
        if args.get("InstanceID", "0") not in ("0", ""):
            raise UpnpActionError(718, "Invalid InstanceID")

    # ---------- AVTransport ----------
    def _avt_set_uri(self, args: dict) -> dict:
        self._instance_id(args)
        uri = args.get("CurrentURI", "").strip()
        if not uri:
            raise UpnpActionError(402, "CurrentURI is empty")
        metadata = args.get("CurrentURIMetaData", "")
        title = extract_title(metadata)
        self.controller.load(uri, metadata, title)
        return {}

    def _avt_play(self, args: dict) -> dict:
        self._instance_id(args)
        self.controller.play()
        return {}

    def _avt_pause(self, args: dict) -> dict:
        self._instance_id(args)
        self.controller.pause()
        return {}

    def _avt_stop(self, args: dict) -> dict:
        self._instance_id(args)
        self.controller.stop()
        return {}

    def _avt_seek(self, args: dict) -> dict:
        self._instance_id(args)
        unit = (args.get("Unit") or "").strip()
        target = (args.get("Target") or "").strip()
        if unit.upper() == "REL_TIME" or ":" in target:
            seconds = parse_upnp_time(target)
            if seconds >= 0:
                self.controller.seek_absolute(seconds)
        return {}

    def _avt_get_transport_info(self, args: dict) -> dict:
        self._instance_id(args)
        return {
            "CurrentTransportState": self.state.get_transport_state(),
            "CurrentTransportStatus": "OK",
            "CurrentSpeed": "1",
        }

    def _avt_get_position_info(self, args: dict) -> dict:
        self._instance_id(args)
        uri, metadata, _title = self.state.get_media()
        pos, dur = self.state.get_progress()
        dur_str = upnp_time(dur) if dur > 0 else "0:00:00.000"
        pos_str = upnp_time(pos)
        has_media = 1 if uri else 0
        return {
            "Track": has_media,
            "TrackDuration": dur_str,
            "TrackMetaData": metadata or "",
            "TrackURI": uri,
            "RelTime": pos_str,
            "AbsTime": pos_str,
            "RelCount": "2147483647",
            "AbsCount": "2147483647",
        }

    def _avt_get_media_info(self, args: dict) -> dict:
        self._instance_id(args)
        uri, metadata, _title = self.state.get_media()
        _pos, dur = self.state.get_progress()
        dur_str = upnp_time(dur) if dur > 0 else "0:00:00.000"
        return {
            "NrTracks": 1 if uri else 0,
            "MediaDuration": dur_str,
            "CurrentURI": uri,
            "CurrentURIMetaData": metadata or "",
            "NextURI": "",
            "NextURIMetaData": "",
            "PlayMedium": "NETWORK",
            "RecordMedium": "NOT_IMPLEMENTED",
            "WriteStatus": "NOT_IMPLEMENTED",
        }

    def _avt_get_capabilities(self, args: dict) -> dict:
        self._instance_id(args)
        return {
            "PossiblePlaybackTransportActions": "Play,Pause,Stop,Seek",
            "PossibleRecordTransportActions": "NOT_IMPLEMENTED",
        }

    def _avt_get_settings(self, args: dict) -> dict:
        self._instance_id(args)
        return {"PlayMode": "NORMAL", "RecQualityMode": "NOT_IMPLEMENTED"}

    def _avt_current_actions(self, args: dict) -> dict:
        self._instance_id(args)
        st = self.state.get_transport_state()
        if st in (RendererState.PLAYING, RendererState.PAUSED_PLAYBACK):
            actions = "Play,Pause,Stop,Seek"
        elif st == RendererState.TRANSITIONING:
            actions = "Stop"
        else:
            actions = "Play,Stop"
        return {"CurrentTransportActions": actions}

    # ---------- ConnectionManager ----------
    def _cm_get_protocol_info(self, args: dict) -> dict:
        return {"Source": "", "Sink": SINK_PROTOCOL_INFO}

    def _cm_get_connection_ids(self, args: dict) -> dict:
        return {"ConnectionIDs": "0"}

    def _cm_get_connection_info(self, args: dict) -> dict:
        return {
            "RcsID": 0,
            "AVTransportID": 0,
            "ProtocolInfo": "",
            "PeerConnectionManager": "",
            "PeerConnectionID": -1,
            "Direction": "Input",
            "Status": "OK",
        }

    # ---------- RenderingControl ----------
    def _rc_get_volume(self, args: dict) -> dict:
        self._instance_id(args)
        return {"CurrentVolume": self.state.get_volume()}

    def _rc_set_volume(self, args: dict) -> dict:
        self._instance_id(args)
        try:
            desired = int(args.get("DesiredVolume", "100"))
        except ValueError:
            raise UpnpActionError(402, "Invalid DesiredVolume")
        self.controller.set_volume(desired)
        return {}

    def _rc_get_mute(self, args: dict) -> dict:
        self._instance_id(args)
        return {"CurrentMute": 0}

    def _rc_set_mute(self, args: dict) -> dict:
        self._instance_id(args)
        return {}

    def _rc_list_presets(self, args: dict) -> dict:
        self._instance_id(args)
        return {"CurrentPresetNameList": "FactoryDefaults"}

    def _rc_select_preset(self, args: dict) -> dict:
        self._instance_id(args)
        return {}
