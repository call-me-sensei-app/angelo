# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Angelo's Linux release build environment: Blender's own (Rocky Linux 8, glibc 2.28, gcc-toolset-14), made by
# upstream's linux_rocky8_setup.sh unchanged. scripts/angelo/build_release_linux_container.sh builds and runs it.
FROM docker.io/library/rockylinux:8
COPY linux_rocky8_setup.sh /tmp/linux_rocky8_setup.sh
RUN bash /tmp/linux_rocky8_setup.sh && dnf -y install binutils elfutils xz && dnf clean all && rm -f /tmp/linux_rocky8_setup.sh
