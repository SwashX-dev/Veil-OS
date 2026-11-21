# Hardware compatibility

Veil uses Tails' kernel, firmware and drivers unchanged, so it **should**
work on the hardware Tails supports (https://tails.net/doc/about/requirements/):
64-bit x86 PCs, 2 GB of RAM (Veil recommends **8 GB**, more for local AI),
and booting from USB.

**Veil doesn't claim universal compatibility.** Status:

| Area | Status |
|---|---|
| UEFI boot (QEMU/OVMF) | see TEST-REPORT.md |
| Legacy BIOS boot (QEMU/SeaBIOS) | see TEST-REPORT.md |
| Physical laptops/desktops (Intel/AMD/NVIDIA GPUs, Wi-Fi chipsets, USB 2/3, SATA/NVMe hosts) | **untested by the Veil project**; inherited from Tails |
| Secure Boot | Tails' signed shim, GRUB and kernel are unchanged, so expected to work; **untested** |
| Vulkan GPU inference (Mesa: AMD/Intel) | built in; **untested on real GPUs** |
| NVIDIA | nouveau only (as in Tails); no proprietary driver, so no CUDA |

Please report results (model, firmware, what worked) in GitHub issues.
