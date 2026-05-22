"""Tests Fase 5: configuración de red y despliegue."""

from __future__ import annotations

from pathlib import Path

from src.config_loader import AppSettings


def test_network_defaults() -> None:
    s = AppSettings()
    assert s.network_mode == "ap_and_lan"
    assert s.ap_ip == "192.168.192.192"
    assert s.ap_ssid_prefix == "kanvis"


def test_deploy_files_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "deploy/systemd/kanvis-edge.service").is_file()
    assert (root / "scripts/kanvis-network.sh").is_file()
    assert (root / "scripts/install.sh").is_file()
    assert (root / "scripts/preflight.sh").is_file()
    assert (root / "scripts/deploy.sh").is_file()
    assert (root / "scripts/lib/ui.sh").is_file()
    assert (root / "scripts/lib/distro.sh").is_file()
    assert (root / "scripts/lib/install-access.sh").is_file()


def test_detect_distro_script() -> None:
    import subprocess

    root = Path(__file__).resolve().parents[1]
    script = root / "scripts/lib/distro.sh"
    out = subprocess.run(
        ["bash", "-c", f"source '{script}'; detect_kanvis_distro"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() in ("debian", "raspberry_pi_os", "unknown")
