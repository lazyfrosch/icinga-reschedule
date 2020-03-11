#!/usr/bin/env python
#
# Icinga Check Re-Scheduler
#
# Copyright (C) 2020 NETWAYS GmbH <info@netways.de>
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
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import time
import logging
import datetime
import os.path

import mysql.connector

MINUTE = 60


class IdoData:
    def __init__(self, username, password, host='localhost', database='icinga', port=None):
        self.host = host
        self.database = database
        self.username = username
        self.password = password
        self.port = port

        self.connection = mysql.connector.connect(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.username,
            password=self.password,
        )

    def __del__(self):
        self.connection.close()

    def fetchall(self, query, params=None):
        assert self.connection is not None
        cur = self.connection.cursor(buffered=False)

        cur.execute(query, params)
        data = cur.fetchall()
        cur.close()

        return data


class CommandPipeSender:
    def __init__(self, path):
        self.path = path

        if not os.path.exists(path):
            raise Exception("Command pipe path does not exist: %s", path)

        # TODO: check for pipe?

    def send_command(self, command, args):
        """
        Low-level implementation for sending the actual command
        """
        line = "[%d] %s;%s\n" % (
            time.time(),
            command,
            ";".join(str(x) for x in args)
        )

        logging.debug("Sending command: %s" % line.strip())

        with open(self.path, mode='a') as handle:
            handle.write(line)

    def schedule_check(self, host, service=None, check_time=None):
        """
        Interface for SCHEDULE_HOST_CHECK and SCHEDULE_SVC_CHECK
        """
        if not check_time:
            check_time = time.time()

        if not service:
            self.send_command('SCHEDULE_HOST_CHECK', [host, check_time])
        else:
            self.send_command('SCHEDULE_SVC_CHECK', [host, service, check_time])


def parse_arguments(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description='Icinga Check Re-scheduler')

    parser.add_argument('--ido-host', metavar='host', help='IDO DB Host', default='localhost')
    parser.add_argument('--ido-port', metavar='port', help='IDO DB Port', type=int)
    parser.add_argument('--ido-database', metavar='database', help='IDO DB Name', default='icinga')
    parser.add_argument('--ido-username', metavar='username', help='IDO DB Username', required=True)
    parser.add_argument('--ido-password', metavar='password', help='IDO DB Password', required=True)

    parser.add_argument('--command-pipe', metavar='path', help='Icinga legacy command pipe', required=True)

    parser.add_argument('--filter-host', metavar='pattern', help='Host name filter pattern')
    parser.add_argument('--filter-service', metavar='pattern', help='Service name filter pattern', required=True)

    parser.add_argument('--period', metavar='minutes', help='Period of time in minutes to lay next_check in',
                        default=60)

    parser.add_argument('--noop', '-n', action='store_true', help='Stop after planning and report')

    parser.add_argument('--debug', '-d', action='store_true', help='Enable debugging output')

    return parser.parse_args(argv)


def human_datetime(time_s):
    dt = datetime.datetime.fromtimestamp(time_s)
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def plan_next_checks(data, period):
    length = len(data)

    period_sec = period * MINUTE
    begin = int(time.time()) + 5 * MINUTE
    end = begin + period_sec

    interval = period_sec / length

    logging.info("Rescheduling %d checks with %d second offset until %s",
                 length, interval, human_datetime(end))

    result = []
    next_check = begin

    for host, service in data:
        logging.debug("Planning time %s for %s!%s", human_datetime(next_check), host, service)
        result.append((host, service, next_check))
        next_check += interval

    return result


def main():
    args = parse_arguments()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(format="[%(levelname)s] %(message)s", level=log_level)

    ido = IdoData(
        host=args.ido_host,
        port=args.ido_port,
        database=args.ido_database,
        username=args.ido_username,
        password=args.ido_password
    )

    # noinspection SqlNoDataSourceInspection,SqlResolve
    query = """
    SELECT
      so.name1 as host,
      so.name2 as service
    FROM icinga_services s
    INNER JOIN icinga_objects so ON so.object_id = s.service_object_id
    WHERE so.is_active = 1 AND so.name2 LIKE %s
    """

    params = [args.filter_service]

    if args.filter_host:
        query += "AND so.name1 LIKE %s"
        params.append(args.filter_host)

    logging.debug("Running search query with params: %s", params)

    services = ido.fetchall(query, params)
    logging.info("Found %d services", len(services))

    plan = plan_next_checks(services, args.period)

    if args.noop:
        for host, service, next_check in plan:
            logging.info("Would set next_check to %s for %s ! %s", human_datetime(next_check), host, service)

        logging.warning("Stopping due to --noop")
        return

    sender = CommandPipeSender(path=args.command_pipe)

    commands = 0
    for host, service, next_check in plan:
        sender.schedule_check(host, service, next_check)
        commands += 1

    logging.info("Sent %d commands to Icinga" % commands)


if __name__ == '__main__':
    main()
