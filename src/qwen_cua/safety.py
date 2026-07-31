from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from .actions import ClickAction, ComputerAction, TypeAction, action_to_public_dict
from .computer import TargetInspection


class UnsafeUrlError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SafetyIntervention:
    kind: str
    title: str
    message: str
    action: dict[str, object]
    element_ref: str
    requires_file: bool = False


def validate_custom_url(url: str, *, allow_private: bool) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("only HTTP(S) custom URLs are supported")
    if not parsed.hostname:
        raise UnsafeUrlError("custom URL must include a hostname")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("credentials embedded in URLs are not allowed")
    if allow_private:
        return
    try:
        addresses = {
            result[4][0] for result in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        }
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"unable to resolve custom URL hostname: {exc}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise UnsafeUrlError(
                "private, loopback, link-local, reserved, and multicast destinations "
                "are blocked by default"
            )


def classify_sensitive_action(
    *,
    action: ComputerAction,
    inspection: TargetInspection | None,
    current_url: str,
) -> SafetyIntervention | None:
    if inspection is None:
        return None
    public_action = action_to_public_dict(
        action,
        redact_text=inspection.is_password,
    )
    if isinstance(action, TypeAction) and inspection.is_password:
        return SafetyIntervention(
            kind="password",
            title="Password entry requires approval",
            message="The model is about to type into a password field. The value is redacted.",
            action=public_action,
            element_ref=inspection.element_ref,
        )
    if isinstance(action, ClickAction) and inspection.is_file:
        return SafetyIntervention(
            kind="file_upload",
            title="Choose a file to upload",
            message="The website requested a local file. Select a file to continue.",
            action=public_action,
            element_ref=inspection.element_ref,
            requires_file=True,
        )
    if isinstance(action, ClickAction) and inspection.download:
        return SafetyIntervention(
            kind="download",
            title="Download requires approval",
            message=f"The model is about to download “{inspection.text or 'a file'}”.",
            action=public_action,
            element_ref=inspection.element_ref,
        )
    if isinstance(action, ClickAction) and inspection.is_submit:
        return SafetyIntervention(
            kind="form_submit",
            title="Form submission requires approval",
            message=f"The model is about to submit “{inspection.text or 'a form'}”.",
            action=public_action,
            element_ref=inspection.element_ref,
        )

    destination = inspection.href or inspection.form_action
    if isinstance(action, ClickAction) and destination:
        current_origin = _origin(current_url)
        destination_origin = _origin(destination)
        if destination_origin and current_origin != destination_origin:
            return SafetyIntervention(
                kind="cross_origin",
                title="Cross-site navigation requires approval",
                message=f"The model is about to navigate to {destination_origin}.",
                action=public_action,
                element_ref=inspection.element_ref,
            )
    return None


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        return ""
    default_port = 443 if parsed.scheme == "https" else 80
    port = parsed.port or default_port
    return f"{parsed.scheme}://{parsed.hostname}:{port}"
