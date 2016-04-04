# -*- coding: utf-8 -*-
#
# Copyright (C) 2015 Red Hat, Inc.
#
# Authors:
# Thomas Woerner <twoerner@redhat.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#


from firewall.core.logger import log
from firewall.functions import checkIPnMask, checkIP6nMask, \
    checkProtocol, enable_ip_forwarding, check_single_address
from firewall.errors import FirewallError, \
    INVALID_IPSET, INVALID_TYPE, IPSET_WITH_TIMEOUT, ALREADY_ENABLED, NOT_ENABLED
from firewall.core.ipset import remove_default_create_options
from firewall.core.io.ipset import IPSet

class FirewallIPSet(object):
    def __init__(self, fw):
        self._fw = fw
        self._ipsets = { }

    def __repr__(self):
        return '%s(%r)' % (self.__class__, self._ipsets)

    def cleanup(self, is_reload=False):
        active = { }
        if is_reload:
            active = self._fw.ipset_backend.get_active_terse()

        for name in self.get_ipsets():
            keep = False
            try:
                obj = self._fw.config.get_ipset(name)
            except FirewallError:
                # no ipset in config interface with this name
                pass
            else:
                if is_reload and "timeout" in obj.options:
                    if name in active and obj.type == active[name][0] and \
                       remove_default_create_options(obj.options) == \
                       active[name][1]:
                        # keep ipset for reload
                        keep = True
            self.remove_ipset(name, keep)

        self._ipsets.clear()

    # ipsets

    def check_ipset(self, name):
        if name not in self.get_ipsets():
            raise FirewallError(INVALID_IPSET, name)

    def get_ipsets(self):
        return sorted(self._ipsets.keys())

    def get_ipset(self, name):
        self.check_ipset(name)
        return self._ipsets[name]

    def _error2warning(self, f, name, *args):
        # transform errors into warnings
        try:
            f(name, *args)
        except FirewallError as error:
            msg = str(error)
            log.warning("%s: %s" % (name, msg))

    def add_ipset(self, obj):
        if obj.type not in self._fw.ipset_supported_types:
            raise FirewallError(INVALID_TYPE,
                                "'%s' is not supported by ipset." % obj.type)
        self._ipsets[obj.name] = obj

    def remove_ipset(self, name, keep=False):
        obj = self._ipsets[name]
        if obj.applied and not keep:
            try:
                self._fw.ipset_backend.destroy(name)
            except Exception as msg:
                log.error("Failed to destroy ipset '%s'" % name)
                log.error(msg)
        else:
            log.debug1("Keeping ipset '%s' because of timeout option", name)
        del self._ipsets[name]

    def apply_ipsets(self, individual=False):
        for name in self.get_ipsets():
            obj = self._ipsets[name]
            obj.applied = False

            if individual:
                try:
                    self._fw.ipset_backend.create(obj.name, obj.type, obj.options)
                except Exception as msg:
                    log.error("Failed to create ipset '%s'" % obj.name)
                    log.error(msg)
                else:
                    obj.applied = True
                    if "timeout" not in obj.options:
                        # no entries visible for ipsets with timeout
                        continue

                for entry in obj.entries:
                    try:
                        self._fw.ipset_backend.add(obj.name, entry)
                    except Exception as msg:
                        log.error("Failed to add entry '%s' to ipset '%s'" % \
                                  (entry, obj.name))
                        log.error(msg)
            else:
                try:
                    self._fw.ipset_backend.restore(obj.name, obj.type, obj.entries,
                                                   obj.options, None)
                except Exception as msg:
                    log.error("Failed to create ipset '%s'" % obj.name)
                    log.error(msg)
                else:
                    obj.applied = True

    # TYPE

    def get_type(self, name):
        return self.get_ipset(name).type

    # OPTIONS

    def get_family(self, name):
        obj = self.get_ipset(name)
        if "family" in obj.options:
            if obj.options["family"] == "inet6":
                return "ipv6"
        return "ipv4"

    # ENTRIES

    def __entry_id(self, entry):
        return entry

    def __entry(self, enable, name, entry):
        pass

    def add_entry(self, name, entry, sender=None):
        obj = self.get_ipset(name)
        if "timeout" in obj.options:
            # no entries visible for ipsets with timeout
            raise FirewallError(IPSET_WITH_TIMEOUT, name)

        IPSet.check_entry(entry, obj.options, obj.type)
        if entry in obj.entries:
            raise FirewallError(ALREADY_ENABLED,
                                "'%s' already is in '%s'" % (entry, name))

        try:
            self._fw.ipset_backend.add(obj.name, entry)
        except Exception as msg:
            log.error("Failed to add entry '%s' to ipset '%s'" % \
                      (entry, obj.name))
            log.error(msg)
        else:
            if "timeout" not in obj.options:
                # no entries visible for ipsets with timeout
                obj.entries.append(entry)

    def remove_entry(self, name, entry, sender=None):
        obj = self.get_ipset(name)
        if "timeout" in obj.options:
            # no entries visible for ipsets with timeout
            raise FirewallError(IPSET_WITH_TIMEOUT, name)

        # no entry check for removal
        if entry not in obj.entries:
            raise FirewallError(NOT_ENABLED,
                                "'%s' not in '%s'" % (entry, name))
        try:
            self._fw.ipset_backend.delete(obj.name, entry)
        except Exception as msg:
            log.error("Failed to remove entry '%s' from ipset '%s'" % \
                      (entry, obj.name))
            log.error(msg)
        else:
            if "timeout" not in obj.options:
                # no entries visible for ipsets with timeout
                obj.entries.remove(entry)

    def query_entry(self, name, entry, sender=None):
        obj = self.get_ipset(name)
        if "timeout" in obj.options:
            # no entries visible for ipsets with timeout
            raise FirewallError(IPSET_WITH_TIMEOUT, name)

        return (entry in obj.entries)

    def get_entries(self, name, sender=None):
        obj = self.get_ipset(name)
        if "timeout" in obj.options:
            # no entries visible for ipsets with timeout
            raise FirewallError(IPSET_WITH_TIMEOUT, name)

        return obj.entries

    def set_entries(self, name, entries, sender=None):
        obj = self.get_ipset(name)
        if "timeout" in obj.options:
            # no entries visible for ipsets with timeout
            raise FirewallError(IPSET_WITH_TIMEOUT, name)

        for entry in entries:
            IPSet.check_entry(entry, obj.options, obj.type)

        for entry in obj.entries:
            try:
                self._fw.ipset_backend.remove(obj.name, entry)
            except Exception as msg:
                log.error("Failed to remove entry '%s' from ipset '%s'" % \
                          (entry, obj.name))
                log.error(msg)
        obj.entries.clear()

        for entry in entries:
            try:
                self._fw.ipset_backend.add(obj.name, entry)
            except Exception as msg:
                log.error("Failed to remove entry '%s' from ipset '%s'" % \
                          (entry, obj.name))
                log.error(msg)
            else:
                obj.entries.append(entry)
