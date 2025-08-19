
# Fedora IoT setup

- [Fedora IoT setup](#fedora-iot-setup)
  - [OS image download](#os-image-download)
  - [Create an ignition config file](#create-an-ignition-config-file)
  - [Serve the ignition file over HTTP](#serve-the-ignition-file-over-http)
  - [Edit the kernel args as the Pi boots](#edit-the-kernel-args-as-the-pi-boots)
  - [Hint: Force Ignition to reapply a config file](#hint-force-ignition-to-reapply-a-config-file)
  - [Install the w1-therm kernel driver for DS18B20 sensors](#install-the-w1-therm-kernel-driver-for-ds18b20-sensors)

## OS image download

The Fedora IoT raw image can be downloaded from https://fedoraproject.org/iot/download.

Choose the aarch64 ‘Raw’ image, not the ‘OSTree’ image, in order to flash it to an SD card
that can be used to boot a Raspberry Pi.

These instructions were prepared for Fedora IoT version **42**.

## Create an ignition config file

An Ignition configuration file can be used to setup WiFi networking, user accounts
and SSH public key authentication.

Reference: [Creating an Ignition configuration
file](https://docs.fedoraproject.org/en-US/iot/creating-an-ignition-configuration-file/)

* Create a `config.bu` ([sample](./config.bu)) Butane configuration file in YAML format.  
  To create a password hash for user account configuration:  
  ```sh
  docker run -it --rm quay.io/coreos/mkpasswd --method=yescrypt
  ```

* Run the Butane tool with Docker (below) in order to convert the Butane
  `config.bu` file (YAML) into an Ignition `config.ign` file (JSON).  
  ```sh
  docker run -i --rm quay.io/coreos/butane:release \
    --pretty --strict < config.bu > config.ign
  ```

## Serve the ignition file over HTTP

As per config in the following sections, the target device will look for an Ignition
file over HTTP. To this end, you can run a trivial web server on a workstation, for
example using Python:

```sh
python -m http.server -d /some/folder 8123
```

Where:
* `/some/folder` is a folder where the `config.ign` file is located.
* `8123` is an arbitrary unused TCP port number (usually between 1024 and 65,535) for the
  HTTP server to listen on.

If your workstation’s operating system has an active firewall, don’t forget to open the
chosen port number for external access.

## Edit the kernel args as the Pi boots

As the Raspberry Pi boots, there will be two fleeting boot menus, first from U-Boot and
then from GRUB. Repeatedly pressing the keyboard’s up and down arrow keys is one way of
getting the boot menus to display _and stay there._ Let the U-Boot menu proceed with the
default option (e.g. ‘mmc 0’), then type ‘e’ during the GRUB boot menu in order to edit
the Linux kernel arguments. Add (type) the following Ignition argument line to the list of
kernel arguments:  

`ignition.config.url=http://192.168.1.2:8123/config.ign`

Where:
* `192.168.1.2:8123` is the IP address and port number of the HTTP server (which could be
  your workstation). Replace these values with your actual details.

After typing the extra argument, press Ctrl-x to proceed with booting the Linux kernel.

## Hint: Force Ignition to reapply a config file

Fedora IoT 42 only applies the Ignition config during _the very first boot_ of the device
after flashing. As such, one would normally have to re-flash the SD card in order to
reapply an Ignition configuration. However, you can force Ignition to reapply the
configuration without re-flashing by adding ‘ignition.firstboot’ to the list of Linux
kernel command line arguments — alongside the ‘ignition.config.url’ argument. For example:

`ignition.firstboot ignition.config.url=...`

## Install the w1-therm kernel driver for DS18B20 sensors

The `w1-therm` kernel driver is part of the `‘kernel-modules-extra’` rpm package. The
package’s full file name has the pattern
`‘kernel-modules-extra-6.14.0-63.fc42.aarch64.rpm’`, where `‘6.14.0-63.fc42.aarch64’` must
match the kernel release string of your Fedora IoT installation, which can obtained with
the `uname -r` command on the target device:

```sh
$ uname -r
6.14.0-63.fc42.aarch64
```

The rpm file can be searched and downloaded from
https://koji.fedoraproject.org/koji/builds or on the command line with `curl`:

```sh
$ curl -O https://kojipkgs.fedoraproject.org/packages/kernel/6.14.0/63.fc42/aarch64/kernel-modules-extra-6.14.0-63.fc42.aarch64.rpm
```

Once downloaded, install the rpm file with the `rpm-ostree` command:

```sh
rpm-ostree install kernel-modules-extra-6.14.0-63.fc42.aarch64.rpm
```

If the installation fails for lack of disk space, a solution may be to enlarge the
partition that backs the `/sysroot` mount point. The following commands worked for me,
executed as the `root` user:

```sh
$ parted /dev/mmcblk0 "resizepart 3 100%"
$ mount --options-mode ignore -o remount,rw /sysroot
$ resize2fs /dev/mmcblk0p3
$ reboot
```

Once `rpm-ostree` succeeds, reboot the system and `w1-therm` should load automatically
(no need for `modprobe w1-therm`).
