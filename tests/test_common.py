import pytest

LIB = "/work/scripts/lib/common.sh"


def load(probe, content, file="/tmp/site.env", extra=""):
    probe.write(file, content)
    return probe.run(f". {LIB}; kk_load_config {file}; {extra}")


def test_load_config_defaults(probe):
    r = load(probe, "COLLECTOR_HOST=192.0.2.10\n",
             extra='echo "$COLLECTOR_HOST $COLLECTOR_PORT $EXPORT_VERSION $CAPTURE_IF '
                   '$MGMT_IF $CAPTURE_RAM_MAX_MB $CAPTURE_SD_MAX_MB $CAPTURE_DIR '
                   '$ENABLE_LUCI [$SYSLOG_HOST] $TRACK_LEVEL"')
    assert r.rc == 0, r.err
    assert r.out.strip() == (
        "192.0.2.10 2055 10 eth1 eth0 100 4096 /mnt/kokabiel-cap 0 [] full"
    )


def test_load_config_rejects_placeholder(probe):
    r = load(probe, "COLLECTOR_HOST=<COLLECTOR_IP>\n")
    assert r.rc == 3
    assert "COLLECTOR_HOST" in r.err


def test_load_config_rejects_missing_host(probe):
    r = load(probe, "COLLECTOR_PORT=2055\n")
    assert r.rc == 3
    assert "COLLECTOR_HOST" in r.err


@pytest.mark.parametrize("mb,rc", [(99, 3), (501, 3), (100, 0), (500, 0)])
def test_load_config_ram_cap_range(probe, mb, rc):
    r = load(probe, f"COLLECTOR_HOST=192.0.2.10\nCAPTURE_RAM_MAX_MB={mb}\n")
    assert r.rc == rc, r.err


@pytest.mark.parametrize("ver,rc", [(8, 3), (9, 0)])
def test_load_config_export_version(probe, ver, rc):
    r = load(probe, f"COLLECTOR_HOST=192.0.2.10\nEXPORT_VERSION={ver}\n")
    assert r.rc == rc, r.err


def test_load_config_crlf_and_quotes(probe):
    r = load(probe, 'COLLECTOR_HOST="192.0.2.10"\r\nCAPTURE_IF="eth1"\r\n',
             extra='printf "%s|%s" "$COLLECTOR_HOST" "$CAPTURE_IF"')
    assert r.rc == 0, r.err
    assert r.out == "192.0.2.10|eth1"


def test_load_config_env_var_selects_file(probe):
    probe.write("/tmp/other.env", "COLLECTOR_HOST=192.0.2.77\n")
    r = probe.run(f". {LIB}; kk_load_config; echo $COLLECTOR_HOST",
                  env={"KOKABIEL_ENV": "/tmp/other.env"})
    assert r.out.strip() == "192.0.2.77"


def test_pkg_install_refuses_kmod(probe):
    r = probe.run(f". {LIB}; kk_pkg_install kmod-fs-ext4")
    assert r.rc == 3
    assert probe.calls("apk") == []


def test_pkg_install_installs_only_missing(probe):
    probe.stub("apk", 'echo "apk $*" >> /tmp/stubs.log\n'
                      '[ "$1" = info ] && [ "$3" = have ] && exit 0\n'
                      '[ "$1" = info ] && exit 1\nexit 0')
    r = probe.run(f". {LIB}; kk_pkg_install have missing")
    assert r.rc == 0, r.err
    adds = [c for c in probe.calls("apk") if c.startswith("apk add")]
    assert adds == ["apk add missing"]


def test_pkg_mgr_prefers_apk(probe):
    r = probe.run(f". {LIB}; kk_pkg_mgr")
    assert r.rc == 0
    assert r.out.strip() == "apk"


def test_svc_missing_is_noop(probe):
    r = probe.run(f". {LIB}; kk_svc nonexistent-svc restart")
    assert r.rc == 0


def test_svc_calls_initd(probe):
    r = probe.run(f". {LIB}; kk_svc dropbear restart")
    assert r.rc == 0
    assert probe.calls("dropbear") == ["dropbear restart"]


def test_sysfs_and_mac(probe):
    probe.fake_nic("eth1", "02:00:00:00:00:AB")
    r = probe.run(f'. {LIB}; kk_sysfs class/net/eth1/address; kk_mac eth1')
    assert r.out.split() == ["/tmp/sys/class/net/eth1/address", "02:00:00:00:00:ab"]


def test_die_exit_code_and_message(probe):
    r = probe.run(f". {LIB}; kk_die boom 7")
    assert r.rc == 7
    assert "kokabiel: boom" in r.err
    assert any(c.startswith("logger -t kokabiel -p daemon.err") for c in probe.calls("logger"))


def test_load_config_unterminated_quote(probe):
    r = load(probe, 'COLLECTOR_HOST=192.0.2.10\nCAPTURE_IF="eth1\n')
    assert r.rc == 3
    assert "line 2" in r.err


def test_load_config_rejects_unquoted_whitespace(probe):
    r = load(probe, "COLLECTOR_HOST=192.0.2.10 extra\n")
    assert r.rc == 3
    assert "line 1" in r.err


def test_load_config_does_not_execute_values(probe):
    r = load(probe, 'COLLECTOR_HOST=192.0.2.10\nPROBE_NAME="$(touch /tmp/pwned)"\n',
             extra='echo "$PROBE_NAME"')
    assert r.rc == 0, r.err
    assert r.out.strip() == "$(touch /tmp/pwned)"
    assert probe.run("test -e /tmp/pwned").rc != 0


def test_load_config_placeholder_with_comment(probe):
    r = load(probe, "COLLECTOR_HOST=<COLLECTOR_IP> # set me\n")
    assert r.rc == 3
    assert "placeholder" in r.err and "COLLECTOR_HOST" in r.err


def test_load_config_comments_and_trailing_comment(probe):
    r = load(probe, '# hi\n\nCOLLECTOR_HOST=192.0.2.10 # c\nCAPTURE_IF="eth1" # c\n',
             extra='echo "$COLLECTOR_HOST $CAPTURE_IF"')
    assert r.rc == 0, r.err
    assert r.out.strip() == "192.0.2.10 eth1"


def test_load_config_disable_services_default_and_override(probe):
    r = load(probe, "COLLECTOR_HOST=192.0.2.10\n", extra='echo "$KOKABIEL_DISABLE_SERVICES"')
    assert r.rc == 0, r.err
    assert r.out.split()[0] == "ttyd" and "odhcpd" in r.out.split()
    r = load(probe, "COLLECTOR_HOST=192.0.2.10\nKOKABIEL_DISABLE_SERVICES='ttyd sqm'\n",
             extra='echo "$KOKABIEL_DISABLE_SERVICES"')
    assert r.out.strip() == "ttyd sqm"


@pytest.mark.parametrize("d", ["/", "/rom", "/overlay", "relative/dir", ""])
def test_load_config_rejects_unsafe_capture_dir(probe, d):
    r = load(probe, f"COLLECTOR_HOST=192.0.2.10\nCAPTURE_DIR={d}\n")
    # an empty value falls back to the default, which is fine
    assert r.rc == (0 if d == "" else 3), r.err
