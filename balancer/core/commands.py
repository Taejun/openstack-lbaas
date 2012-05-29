# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012 OpenStack LLC.
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import functools
import logging
import types

from balancer.common import utils
from balancer.storage import storage

LOG = logging.getLogger(__name__)


class RollbackContext(object):
    def __init__(self):
        self.rollback_stack = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        good = exc_type is None
        if not good:
            LOG.error("Rollback because of: %s", exc_value)
        while self.rollback_stack:
            self.rollback_stack.pop()(good)

    def add_rollback(self, rollback):
        try:
            self.rollback_stack.append(rollback)
        except Rollback:
            pass
        except Exception:
            LOG.exception("Exception during rollback.")


class Rollback(Exception):
    pass


def with_rollback(func):
    @functools.wraps(func)
    def __inner(ctx, *args, **kwargs):
        gen = func(ctx, *args, **kwargs)
        if not isinstance(gen, types.GeneratorType):
            LOG.critical("Expected generator, got %r instead", gen)
            raise RuntimeError(
                    "Commands with rollback must be generator functions")
        try:
            res = gen.next()
        except StopIteration:
            LOG.warn("Command %s finished w/o yielding", func.__name__)
        else:
            def fin(good):
                if good:
                    gen.close()
                else:
                    gen.throw(Rollback)
            ctx.add_rollback(fin)
        return res
    return __inner


@with_rollback
def create_rserver(ctx, conf, driver, rs):
    try:
        # We can't create multiple RS with the same IP. So parent_id points to
        # RS which already deployed and has this IP
        LOG.debug("Creating rserver command execution with rserver: %s", rs)
        LOG.debug("RServer parent_id: %s", rs.parent_id)
        if rs.parent_id == "":
            driver.createRServer(ctx, rs)
            rs.deployed = 'True'
            stor = storage.Storage(conf)
            wr = stor.getWriter()
            wr.updateDeployed(rs, 'True')
        yield
    except Exception:
        driver.deleteRServer(ctx, rs)
        rs.deployed = 'False'
        stor = storage.Storage(conf)
        wr = stor.getWriter()
        wr.updateDeployed(rs, 'False')
        raise


def delete_rserver(ctx, conf, driver, rs):
    store = storage.Storage(conf)
    reader = store.getReader()
    rss = []
    LOG.debug("Got delete RS request")
    if rs.parent_id != "" and utils.checkNone(rs.parent_id):
        rss = reader.getRServersByParentID(rs.parent_id)
        LOG.debug("List servers %s", rss)
        if len(rss) == 1:
            parent_rs = reader.getRServerById(rs.parent_id)
            driver.deleteRServer(ctx, parent_rs)
    else:
        # rs1
        # We need to check if there are reals who reference this rs as a parent
        rss = reader.getRServersByParentID(rs.id)
        LOG.debug("List servers %s", rss)
        if len(rss) == 0:
            driver.deleteRServer(ctx, rs)


def create_sticky(ctx, conf, driver, sticky):
    driver.createStickiness(ctx, sticky)
    sticky.deployed = 'True'
    stor = storage.Storage(conf)
    wr = stor.getWriter()
    wr.updateDeployed(sticky, 'True')


def delete_sticky(ctx, conf, driver, sticky):
    driver.deleteStickiness(ctx, sticky)
    sticky.deployed = 'False'
    stor = storage.Storage(conf)
    wr = stor.getWriter()
    wr.updateDeployed(sticky, 'False')


@with_rollback
def create_server_farm(ctx, conf, driver, sf):
    try:
        driver.createServerFarm(ctx, sf)
        sf.deployed = 'True'
        stor = storage.Storage(conf)
        wr = stor.getWriter()
        wr.updateDeployed(sf, 'True')
        yield
    except Exception:
        driver.deleteServerFarm(ctx, sf)
        sf.deployed = 'False'
        stor = storage.Storage(conf)
        wr = stor.getWriter()
        wr.updateDeployed(sf, 'False')


def delete_server_farm(ctx, conf, driver, sf):
    driver.deleteServerFarm(ctx, sf)
    sf.deployed = 'False'
    stor = storage.Storage(conf)
    wr = stor.getWriter()
    wr.updateDeployed(sf, 'False')


@with_rollback
def add_rserver_to_server_farm(ctx, conf, driver, server_farm, rserver):
    try:
        if rserver.parent_id != "":
            #Nasty hack. We need to think how todo this more elegant
            rserver.name = rserver.parent_id

        driver.addRServerToSF(ctx, server_farm, rserver)
        yield
    except Exception:
        driver.deleteRServerFromSF(ctx, server_farm, rserver)


def delete_rserver_from_server_farm(ctx, conf, driver, server_farm, rserver):
    driver.deleteRServerFromSF(ctx, server_farm, rserver)


@with_rollback
def create_probe(ctx, conf, driver, probe):
    try:
        driver.createProbe(ctx, probe)
        probe.deployed = 'True'
        stor = storage.Storage(conf)
        wr = stor.getWriter()
        wr.updateDeployed(probe, 'True')
        yield
    except Exception:
        driver.deleteProbe(ctx, probe)
        probe.deployed = 'False'
        stor = storage.Storage(conf)
        wr = stor.getWriter()
        wr.updateDeployed(probe, 'False')


def delete_probe(ctx, conf, driver, probe):
    driver.deleteProbe(ctx, probe)
    probe.deployed = 'False'
    stor = storage.Storage(conf)
    wr = stor.getWriter()
    wr.updateDeployed(probe, 'False')


@with_rollback
def add_probe_to_server_farm(ctx, conf, driver, server_farm, probe):
    try:
        driver.addProbeToSF(ctx, server_farm, probe)
        yield
    except Exception:
        driver.deleteProbeFromSF(ctx, server_farm, probe)


@with_rollback
def delete_probe_from_server_farm(ctx, conf, driver, server_farm, probe):
    driver.deleteProbeFromSF(ctx, server_farm, probe)


def activate_rserver(ctx, conf, driver, server_farm, rserver):
    driver.activateRServer(ctx, server_farm, rserver)


def suspend_rserver(ctx, conf, driver, server_farm, rserver):
    driver.suspendRServer(ctx, server_farm, rserver)


@with_rollback
def create_vip(ctx, conf, driver, vip, server_farm):
    try:
        driver.createVIP(ctx, vip, server_farm)
        vip.deployed = 'True'
        stor = storage.Storage(conf)
        wr = stor.getWriter()
        wr.updateDeployed(vip, 'True')
        yield
    except Exception:
        driver.deleteVIP(ctx, vip, server_farm)
        vip.deployed = 'False'
        stor = storage.Storage(conf)
        wr = stor.getWriter()
        wr.updateDeployed(vip, 'False')


def delete_vip(ctx, conf, driver, vip):
    driver.deleteVIP(ctx, vip)
    vip.deployed = 'False'
    stor = storage.Storage(conf)
    wr = stor.getWriter()
    wr.updateDeployed(vip, 'False')
