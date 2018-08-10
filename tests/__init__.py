# Copyright (C) 2017 Red Hat, Inc., Bryn M. Reeves <bmr@redhat.com>
#
# boom/__init__.py - Boom package initialisation
#
# This file is part of the boom project.
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions
# of the GNU General Public License v.2.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

class MockArgs(object):
    """Mock arguments class for testing boom command line infrastructure.
    """
    boot_id = "12345678"
    title = ""
    version = ""
    machine_id = ""
    linux = ""
    initrd = ""
    efi = ""
    root_device = ""
    root_lv = ""
    btrfs_subvolume = "23"
    os_id = ""
    name = ""
    short_name = ""
    os_version = ""
    os_version_id = ""
    os_options = ""
    profile = ""
    uname_pattern = ""
    host_profile = ""
    label = ""

# vim: set et ts=4 sw=4 :
