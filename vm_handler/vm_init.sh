PUBKEY="$(cat .vm/seed/testkey.pub)"

cat > .vm/seed/user-data <<'YAML'
#cloud-config
ssh_authorized_keys:
  - __PUBKEY__
disable_root: false
ssh_pwauth: false

package_update: true
packages:
  - openssh

# Alpine + ifupdown-ng networking (ensure DHCP on eth0)
write_files:
  - path: /etc/network/interfaces
    content: |
      auto lo
      iface lo inet loopback
      auto eth0
      iface eth0 inet dhcp

runcmd:
  # bring up net (idempotent)
  - rc-service networking restart || rc-service networking start || true

  # enable SSH on boot and start now
  - rc-update add sshd default || true
  - rc-service sshd restart || rc-service sshd start || true

  # make sure serial logins exist for both common consoles
  - sed -i 's/^[#]*ttyS0.*/ttyS0::respawn:\/sbin\/getty -L ttyS0 115200 vt100/' /etc/inittab
  - grep -q '^ttyAMA0::' /etc/inittab || echo 'ttyAMA0::respawn:/sbin/getty -L ttyAMA0 115200 vt100' >> /etc/inittab
  - kill -HUP 1 || true
YAML

# inject your pubkey
sed -i '' "s|__PUBKEY__|${PUBKEY//|/\\|}|" .vm/seed/user-data

# rebuild the seed.iso (volume label must be 'cidata')
mkisofs -output .vm/seed/seed.iso -volid cidata -joliet -rock \
  .vm/seed/user-data .vm/seed/meta-data
