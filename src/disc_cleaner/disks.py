from __future__ import annotations

import os
from dataclasses import dataclass

REAL_FS = {"ext2", "ext3", "ext4", "btrfs", "xfs", "zfs", "f2fs", "vfat", "exfat", "ntfs", "ntfs3", "fuseblk", "bcachefs"}


@dataclass
class Disk:
    device: str
    mount: str
    total: int
    used: int
    free: int

    @property
    def percent(self) -> float:
        return self.used / self.total * 100 if self.total else 0.0


def disks() -> list[Disk]:
    seen, result = set(), []
    with open("/proc/mounts") as f:
        for line in f:
            dev, mount, fstype, *_ = line.split()
            mount = mount.replace("\\040", " ")
            if fstype not in REAL_FS or dev in seen or mount.startswith("/snap"):
                continue
            try:
                st = os.statvfs(mount)
            except OSError:
                continue
            seen.add(dev)
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            result.append(Disk(dev, mount, total, total - st.f_bfree * st.f_frsize, free))
    return result
