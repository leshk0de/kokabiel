import json

ENV = {"KOKABIEL_LIB": "/work/scripts/lib", "KOKABIEL_SD_SETTLE": "0",
       "CAPTURE_DIR": "/mnt/kokabiel-cap"}
SD = "/work/scripts/sd.sh"
FMT = "/work/scripts/format-sd.sh"
PROMPT = ("Erase SD card 3776MB 0x00000001 on probe-a and use it for captures? "
          "Type ERASE to confirm:")

BLKID = """#!/bin/sh
echo "blkid $*" >> /tmp/stubs.log
case "$1" in
/dev/mmcblk0p1) echo '/dev/mmcblk0p1: LABEL="boot" UUID="AAAA-0001" TYPE="vfat"' ;;
/dev/mmcblk0p2) echo '/dev/mmcblk0p2: LABEL="rootfs" UUID="00000000-0000-4000-8000-000000000002" TYPE="ext4"' ;;
/dev/mmcblk1p1) echo '/dev/mmcblk1p1: LABEL="KOKABIEL-CAP" UUID="00000000-0000-4000-8000-0000000000c1" TYPE="ext4"' ;;
esac
exit 0
"""

# mount stub: a read-only mount of a Kokabiel card exposes the marker.
MOUNT_MARKER = """#!/bin/sh
echo "mount $*" >> /tmp/stubs.log
for a; do d=$a; done
touch "$d/.kokabiel"
exit 0
"""


def sd(probe, args, env=None, stdin=None):
    return probe.run(f"{SD} {args}", env={**ENV, **(env or {})}, stdin=stdin)


def fmt(probe, args, stdin=None, node=True, env=None):
    # sfdisk would create the partition node; the tests only need it to exist
    probe.run("touch /dev/mmcblk0p1" if node else "rm -f /dev/mmcblk0p1")
    return probe.run(f"{FMT} {args}", env={**ENV, "KOKABIEL_SD_NODE_TEST": "-e", **(env or {})},
                     stdin=stdin)


def test_sd_find_ignores_emmc(probe):
    probe.mountinfo()
    probe.fake_mmc("mmcblk2", "MMC", 61071360, partitions=["mmcblk2p1"])
    probe.fake_mmc("mmcblk0", "SD", 7733248)
    r = sd(probe, "find")
    assert r.rc == 0 and r.out.strip() == "/dev/mmcblk0"


def test_sd_find_refuses_root_device(probe):
    probe.fake_mmc("mmcblk0", "SD", 7733248, partitions=["mmcblk0p2"])
    probe.mountinfo("36 35 179:2 / /overlay rw - ext4 /dev/mmcblk0p2 rw")
    r = sd(probe, "find")
    assert r.rc == 1 and r.out.strip() == ""


def test_sd_describe_absent(probe):
    probe.mountinfo()
    r = sd(probe, "describe")
    assert r.rc == 0
    d = json.loads(r.out)
    assert d["present"] is False and d["device"] is None
    assert d["kokabiel"] is False and d["partitions"] == []


def test_sd_describe_foreign_card(probe):
    probe.mountinfo()
    probe.stub("blkid", BLKID)
    probe.fake_mmc("mmcblk0", "SD", 7733248, partitions=["mmcblk0p1", "mmcblk0p2"])
    r = sd(probe, "describe")
    d = json.loads(r.out)
    assert d["present"] is True and d["device"] == "/dev/mmcblk0"
    assert d["kokabiel"] is False
    assert d["size_mb"] == 3776 and d["serial"] == "0x00000001"
    assert [p["label"] for p in d["partitions"]] == ["boot", "rootfs"]
    assert d["partitions"][0]["fstype"] == "vfat"
    assert len(r.out.strip().splitlines()) == 1


def kokabiel_card(probe, marker=True):
    probe.run("rm -rf /tmp/sdtmp")  # survives probe.reset(); a stale marker would lie
    probe.mountinfo()
    probe.stub("blkid", BLKID)
    probe.stub("mount", MOUNT_MARKER if marker else "echo \"mount $*\" >> /tmp/stubs.log")
    probe.fake_mmc("mmcblk1", "SD", 7733248, partitions=["mmcblk1p1"])


def test_sd_describe_kokabiel_card(probe):
    kokabiel_card(probe)
    d = json.loads(sd(probe, "describe", env={"KOKABIEL_SD_TMP": "/tmp/sdtmp"}).out)
    assert d["kokabiel"] is True
    assert any(c.startswith("mount -o ro /dev/mmcblk1p1") for c in probe.calls("mount"))
    assert probe.calls("umount")


def test_sd_label_without_marker_is_not_kokabiel(probe):
    kokabiel_card(probe, marker=False)
    d = json.loads(sd(probe, "describe", env={"KOKABIEL_SD_TMP": "/tmp/sdtmp"}).out)
    assert d["kokabiel"] is False
    assert d["partitions"][0]["label"] == "KOKABIEL-CAP"


def test_sd_mount_writes_fstab_once(probe):
    kokabiel_card(probe)
    for _ in range(2):
        assert sd(probe, "mount", env={"KOKABIEL_SD_TMP": "/tmp/sdtmp"}).rc == 0
    out = probe.run("uci show fstab | grep -c '^fstab.kokabiel_cap='").out.strip()
    assert out == "1"
    assert probe.uci("fstab.kokabiel_cap.options") == "noatime"
    assert probe.uci("fstab.kokabiel_cap.uuid") == "00000000-0000-4000-8000-0000000000c1"
    assert probe.uci("fstab.kokabiel_cap.target") == "/mnt/kokabiel-cap"
    assert probe.uci("fstab.kokabiel_cap.enabled") == "1"
    assert probe.calls("block")


def test_sd_mount_removes_section_when_card_gone(probe):
    kokabiel_card(probe)
    sd(probe, "mount", env={"KOKABIEL_SD_TMP": "/tmp/sdtmp"})
    probe.run("rm -rf /tmp/sys/block/mmcblk1")
    assert sd(probe, "mount").rc == 0
    assert probe.uci("fstab.kokabiel_cap") == ""


def test_sd_is_mounted(probe):
    assert sd(probe, "is-mounted").rc == 1
    probe.write("/tmp/proc/mounts", "/dev/mmcblk1p1 /mnt/kokabiel-cap ext4 rw 0 0\n")
    assert sd(probe, "is-mounted").rc == 0
    probe.write("/tmp/proc/mounts", "/dev/mmcblk1p1 /mnt/kokabiel-cap2 ext4 rw 0 0\n")
    assert sd(probe, "is-mounted").rc == 1


def test_sd_bad_subcommand(probe):
    assert sd(probe, "bogus").rc == 3


def one_card(probe):
    probe.mountinfo()
    probe.fake_mmc("mmcblk0", "SD", 7733248)


def destructive(probe):
    return [c for c in probe.calls() if c.split()[0] in ("dd", "sfdisk", "mkfs.ext4")]


def test_format_requires_exact_erase(probe):
    one_card(probe)
    r = fmt(probe, "--device /dev/mmcblk0 --probe probe-a", stdin="erase\n")
    assert r.rc == 7
    assert PROMPT in r.out
    assert destructive(probe) == []


def test_format_with_confirm_runs_steps_in_order(probe):
    one_card(probe)
    r = fmt(probe, "--device /dev/mmcblk0 --confirm ERASE --probe probe-a")
    assert r.rc == 0, r.err
    d = destructive(probe)
    assert len(d) == 3
    assert d[0].startswith("dd if=/dev/zero of=/dev/mmcblk0") and "count=16" in d[0]
    assert d[1] == "sfdisk /dev/mmcblk0"
    assert d[2] == "mkfs.ext4 -F -L KOKABIEL-CAP /dev/mmcblk0p1"
    assert probe.calls("mount") and probe.calls("umount")


def test_format_prompt_answer_erase_proceeds(probe):
    one_card(probe)
    r = fmt(probe, "--device /dev/mmcblk0 --probe probe-a", stdin="ERASE\n")
    assert r.rc == 0, r.err
    assert PROMPT in r.out and len(destructive(probe)) == 3


def test_format_refuses_emmc(probe):
    probe.mountinfo()
    probe.fake_mmc("mmcblk2", "MMC", 61071360)
    r = fmt(probe, "--device /dev/mmcblk2 --confirm ERASE")
    assert r.rc == 6 and destructive(probe) == []


def test_format_refuses_root_device(probe):
    probe.fake_mmc("mmcblk0", "SD", 7733248, partitions=["mmcblk0p2"])
    probe.mountinfo("36 35 179:2 / / rw - ext4 /dev/mmcblk0p2 rw")
    r = fmt(probe, "--device /dev/mmcblk0 --confirm ERASE")
    assert r.rc == 6 and destructive(probe) == []


# ---- review round 1 ----------------------------------------------------------

def unsafe(probe, mi, cmdline=None, parts=("mmcblk0p1", "mmcblk0p2"), extra=None):
    probe.fake_mmc("mmcblk0", "SD", 7733248, partitions=list(parts))
    if mi is not None:
        probe.mountinfo(*mi)
    if cmdline is not None:
        probe.write("/tmp/proc/cmdline", cmdline)
    if extra:
        extra()
    return sd(probe, "find")


def test_guard_dev_root_rom_with_cmdline_root(probe):
    r = unsafe(probe, ["20 1 0:15 / /rom ro - squashfs /dev/root ro"],
               cmdline="console=ttyS2 root=/dev/mmcblk0p2 rootwait\n")
    assert r.rc == 1 and r.out.strip() == ""


def test_guard_cmdline_partuuid(probe):
    probe.stub("blkid", "#!/bin/sh\n"
               "[ \"$1\" = /dev/mmcblk0p2 ] && echo '/dev/mmcblk0p2: PARTUUID=\"ABCD0001-02\"'\n")
    r = unsafe(probe, [], cmdline="root=PARTUUID=abcd0001-02 rootwait\n")
    assert r.rc == 1


def test_guard_root_by_device_number(probe):
    # source is not a /dev/mmcblk path at all; the major:minor gives it away
    r = unsafe(probe, ["36 35 179:2 / / rw - ext4 /dev/root rw"])
    assert r.rc == 1


def test_guard_loop_backed_overlay(probe):
    def loop():
        probe.write("/tmp/sys/block/loop0/dev", "7:0\n")
        probe.write("/tmp/sys/block/loop0/loop/backing_file", "/mnt/data/overlay.img\n")
    r = unsafe(probe, ["30 1 7:0 / /overlay rw - f2fs /dev/loop0 rw",
                       "31 1 179:2 / /mnt/data rw - ext4 /dev/disk1 rw"], extra=loop)
    assert r.rc == 1


def test_guard_loop_backing_file_on_card_even_if_partition_name_hidden(probe):
    def loop():
        probe.write("/tmp/sys/block/loop0/dev", "7:0\n")
        probe.write("/tmp/sys/block/loop0/loop/backing_file", "/mnt/data/overlay.img\n")
    # only the loop is mounted; its backing file's mount (on the card) is via "/"
    r = unsafe(probe, ["30 1 7:0 / /overlay rw - f2fs /dev/loop0 rw",
                       "1 0 179:2 / / rw - ext4 /dev/disk1 rw"], extra=loop)
    assert r.rc == 1


def test_guard_partition_mounted_elsewhere(probe):
    r = unsafe(probe, ["36 35 179:1 / /opt rw - ext4 /dev/mmcblk0p1 rw"])
    assert r.rc == 1


def test_guard_overlay_with_data_partition_elsewhere(probe):
    r = unsafe(probe, ["10 1 0:30 / / rw - overlay overlay rw,lowerdir=/root,upperdir=/data/root",
                       "36 35 179:2 / /data rw - ext4 /dev/mmcblk0p2 rw"])
    assert r.rc == 1


def test_guard_missing_mountinfo_fails_closed(probe):
    r = unsafe(probe, None)
    assert r.rc == 1


def test_guard_other_disk_mounts_do_not_matter(probe):
    probe.fake_mmc("mmcblk2", "MMC", 61071360, partitions=["mmcblk2p8"])
    r = unsafe(probe, ["36 35 179:24 / /overlay rw - f2fs /dev/mmcblk2p8 rw"])
    assert r.rc == 0 and r.out.strip() == "/dev/mmcblk0"


def test_guard_card_with_no_mounts_is_safe(probe):
    assert unsafe(probe, []).rc == 0


def test_guard_card_mounted_only_at_capture_dir_is_safe(probe):
    r = unsafe(probe, ["36 35 179:1 / /mnt/kokabiel-cap rw - ext4 /dev/mmcblk0p1 rw"])
    assert r.rc == 0


def test_format_refuses_unsafe_by_device_number(probe):
    probe.mountinfo("36 35 179:2 / / rw - ext4 /dev/root rw")
    probe.fake_mmc("mmcblk0", "SD", 7733248, partitions=["mmcblk0p2"])
    r = fmt(probe, "--device /dev/mmcblk0 --confirm ERASE")
    assert r.rc == 6 and destructive(probe) == []


def test_format_rechecks_safety_after_confirmation(probe):
    one_card(probe)
    probe.write("/tmp/proc/cmdline", "console=ttyS2\n")
    cmd = ("(sleep 1; echo '36 35 179:1 / /opt rw - ext4 /dev/mmcblk0p1 rw' "
           "> /tmp/proc/self/mountinfo) & (sleep 2; echo ERASE) | "
           f"{FMT} --device /dev/mmcblk0 --probe probe-a; r=$?; wait; exit $r")
    probe.run("touch /dev/mmcblk0p1")
    probe.fake_mmc("mmcblk0", "SD", 7733248, partitions=["mmcblk0p1"])
    r = probe.run(cmd, env={**ENV, "KOKABIEL_SD_NODE_TEST": "-e"})
    assert r.rc == 6, r.err
    assert destructive(probe) == [] and not probe.calls("umount")


def test_format_rejects_erase_with_whitespace_empty_and_eof(probe):
    one_card(probe)
    for answer in ("ERASE \n", " ERASE\n", "\n", "", "ERASE"):
        r = fmt(probe, "--device /dev/mmcblk0 --probe probe-a", stdin=answer)
        assert r.rc == 7, (answer, r.err)
        assert destructive(probe) == []


def test_format_fails_closed_when_partition_node_never_appears(probe):
    one_card(probe)
    r = fmt(probe, "--device /dev/mmcblk0 --confirm ERASE --probe probe-a", node=False)
    assert r.rc == 3
    assert not probe.calls("mkfs.ext4")
    assert probe.calls("sfdisk")


def test_format_rejects_bad_device_args(probe):
    one_card(probe)
    probe.fake_mmc("mmcblk0", "SD", 7733248, partitions=["mmcblk0p1"])
    for dev in ("/dev/mmcblk0p1", "mmcblk0", "/dev/mmcblk0x"):
        r = fmt(probe, f"--device {dev} --confirm ERASE")
        assert r.rc == 6, dev
        assert destructive(probe) == []


def test_describe_label_with_quote_and_space(probe):
    probe.mountinfo()
    probe.stub("blkid", "#!/bin/sh\n"
               "echo '/dev/mmcblk0p1: LABEL=\"my \\\"x\\\" card\" UUID=\"AAAA-0001\" TYPE=\"vfat\"'\n")
    probe.fake_mmc("mmcblk0", "SD", 7733248, partitions=["mmcblk0p1"])
    d = json.loads(sd(probe, "describe").out)
    assert d["partitions"][0]["label"] == 'my "x" card'
    assert d["partitions"][0]["uuid"] == "AAAA-0001"


def mounted_card(probe):
    probe.stub("blkid", BLKID)
    probe.run("mkdir -p /tmp/capmnt && touch /tmp/capmnt/.kokabiel")
    probe.fake_mmc("mmcblk1", "SD", 7733248, partitions=["mmcblk1p1"])
    probe.write("/tmp/proc/mounts", "/dev/mmcblk1p1 /tmp/capmnt ext4 rw 0 0\n")
    probe.mountinfo("36 35 179:9 / /tmp/capmnt rw - ext4 /dev/mmcblk1p1 rw")


def test_describe_already_mounted_card_uses_live_mount(probe):
    mounted_card(probe)
    d = json.loads(sd(probe, "describe", env={"CAPTURE_DIR": "/tmp/capmnt"}).out)
    assert d["kokabiel"] is True
    assert not probe.calls("mount")


def test_sd_mount_keeps_fstab_when_card_already_mounted(probe):
    mounted_card(probe)
    probe.run("uci set fstab.kokabiel_cap=mount; uci set fstab.kokabiel_cap.options=noatime; "
              "uci commit fstab")
    assert sd(probe, "mount", env={"CAPTURE_DIR": "/tmp/capmnt"}).rc == 0
    assert probe.uci("fstab.kokabiel_cap.uuid") == "00000000-0000-4000-8000-0000000000c1"
