# Console bootstrap

A fresh FriendlyWrt image answers ping on its WAN port but rejects SSH: the OpenWrt `wan` zone
rejects input, and dropbear may be bound to the `lan` interface only. These steps, typed once on
the local console (keyboard and monitor attached to the probe), make SSH reachable on the WAN
(management) port. Everything after that runs over SSH from Ansible.

> **WARNING: do not cable the LAN / capture port (eth1) yet.** Out of the box FriendlyWrt runs a
> DHCP server (and IPv6 router advertisements) on that port. Plugging it into your home switch
> before Kokabiel's setup has stripped that role would hand out wrong addresses to your whole
> network. Cable only the WAN / management port (eth0). Connect eth1 after the playbook has run
> and `kokabiel-status` confirms the capture port has no address (setup disables its DHCP/RA
> server; status does not check that, so cable it only after the play has completed).

## 1. Log in

Log in on the console as `root`. The password on a fresh image is `password` or empty, depending on
the image.

## 2. Set a temporary root password

Some images ship with an empty password, and dropbear refuses blank passwords over SSH.

```sh
passwd
```

This password is temporary: the Ansible play replaces it with a generated passphrase on its first
run. Use it for `--ask-pass` once.

## 3. Open SSH on the WAN side

These commands are idempotent. Paste them as a block. They are the same changes `setup.sh` makes
persistently later; the bootstrap only exists so that the first SSH connection is possible.

```sh
# dropbear: remove any Interface binding so it listens on all interfaces
uci -q delete 'dropbear.@dropbear[0].Interface'
uci commit dropbear

# firewall: allow TCP 22 from the wan zone
uci set firewall.allow_ssh_wan=rule
uci set firewall.allow_ssh_wan.name='Allow-SSH-WAN'
uci set firewall.allow_ssh_wan.src='wan'
uci set firewall.allow_ssh_wan.proto='tcp'
uci set firewall.allow_ssh_wan.dest_port='22'
uci set firewall.allow_ssh_wan.target='ACCEPT'
uci commit firewall

/etc/init.d/dropbear restart
/etc/init.d/firewall restart
```

## 4. Check it worked

```sh
cat /etc/openwrt_release          # OpenWrt version (expect 25.12.x on current FriendlyWrt)
ip -br addr                       # note eth0's address: this is the probe's management address
netstat -tln | grep ':22 '        # dropbear listening, on 0.0.0.0 / :: rather than one address
uci show firewall.allow_ssh_wan   # the rule you just added
```

`ip -br addr` also shows how to find the WAN address that Ansible needs as `ansible_host`: it is
the IPv4 address on `eth0`, obtained by DHCP from your network. Giving it a DHCP reservation on
your router keeps it stable.

From your own machine, `ssh root@<probe-address>` should now prompt for the temporary password.

## 5. Install your SSH key (optional here)

The Ansible play installs your key for you (default `~/.ssh/id_ed25519.pub`, which must be a
single public-key line; override with `ssh_pubkey`). Host key checking is on: SSH to the probe
once (or `ssh-keyscan`) before the first Ansible run. If you want key access before running it:

```sh
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@<probe-address>
```

or, on the console, as a one-liner (dropbear reads `/etc/dropbear/authorized_keys`):

```sh
echo 'ssh-ed25519 AAAA... you@host' >> /etc/dropbear/authorized_keys && chmod 600 /etc/dropbear/authorized_keys
```

## Next

Back to the [README quick start](../README.md#quick-start).
