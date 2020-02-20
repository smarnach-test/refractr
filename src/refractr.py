#!/usr/bin/env python3

import os
import re
import sys
import toml

from urllib import parse
from itertools import chain

from leatherman.fuzzy import fuzzy
from leatherman.yaml import yaml_format
from leatherman.dictionary import head, body, head_body
from leatherman.repr import __repr__
from leatherman.dbg import dbg

from utils import *

setup_yaml()

HTTP_PORT = 80
HTTPS_PORT = 443

class DomainPathMismatchError(Exception):
    def __init__(self, domains, paths):
        msg = f'domains|paths mismatch error; the product must match; domains={domains} paths={paths}'
        super().__init__(msg)

class LoadRefractError(Exception):
    def __init__(self, dst):
        msg = f'load refract error; dst={dst}'
        super().__init__(msg)

class RefractSpecError(Exception):
    def __init__(self, spec):
        msg = f'refract spec error; spec={spec}'
        super().__init__(msg)

class RefractrConfig:
    def __init__(self, spec):
        self.refracts = [Refract(**spec) for spec in spec['refracts']]

    def __str__(self):
        return yaml_format(self.json())

    def json(self):
        return dict(refracts=[refract.json() for refract in self.refracts])

    def render(self):
        stanzas = list(chain(*[refract.render() for refract in self.refracts]))
        return '\n'.join([repr(stanza) for stanza in stanzas if stanza])

    def validate(self):
        return dict(refracts=[refract.validate() for refract in self.refracts])

class Refract:
    def __init__(self, dst=None, srcs=None, nginx=None, tests=None, status=None):
        self.dst = dst
        self.srcs = srcs
        self.nginx = nginx
        self._tests = tests
        self.status = status

    @property
    def src(self):
        if self.srcs:
            return self.srcs[0]

    @property
    def server_name(self):
        return join(domains(self.srcs))

    @property
    def is_simple_dst(self):
        return isinstance(self.dst, str)

    @property
    def is_http_dst(self):
        return self.is_simple_dst and self.dst.startswith('http://')

    @property
    def is_rewrite(self):
        if isinstance(self.dst, (list, tuple)):
            return any([startswith(k, '^', 'if') for d in self.dst for k,v in d.items()])
        return False

    @property
    def tests(self):
        if self._tests:
            return self._tests
        tests = []
        if not self.is_rewrite:
            for src in self.srcs:
                given = f'http://{src}'
                if is_list_of_dicts(self.dst):
                    for item in self.dst:
                        try:
                            location, target = head_body(item)
                            tests += [{f'{given}{location}': target}]
                        except:
                            continue
                elif is_scalar(self.dst):
                    tests = [{given: self.dst}]
                else:
                    raise LoadRefractError(self.dst)
        return tests

    def listen(self, port):
        return port, f'[::]:{port}'

    def json(self, **kwargs):
        json = dict(tests=self.tests)
        if self.nginx:
            json.update(dict(nginx=self.nginx))
        else:
            json.update(dict(
                srcs=self.srcs,
                dst=self.dst,
                status=self.status))
        json.update(**kwargs)
        return json

    def __str__(self):
        return yaml_format(self.json())

    def render_http_to_https(self, target='https://$host$request_uri'):
        return Section(
            'server',
            kvo('server_name', self.server_name),
            dups('listen', *self.listen(HTTP_PORT)),
            kmvo('return', self.status, target)
        )

    def render_redirect(self):
        server_name = kvo('server_name', self.server_name)
        listen = dups('listen', *self.listen(HTTPS_PORT))
        if is_list_of_dicts(self.dst):
            locations = []
            for dst in self.dst:
                path, target = head_body(dst)
                locations += [Location(
                    path,
                    kmvo('return', self.status, target)
                )]
            return Section(
                'server',
                server_name,
                listen,
                *locations,
            )

        return Section(
            'server',
            server_name,
            listen,
            kmvo('return', self.status, self.dst),
        )

    def render_rewrite(self):
        server_name = kvo('server_name', self.server_name)
        listen = dups('listen', *self.listen(HTTPS_PORT))
        rewrites = []
        for dst in self.dst:
            if_ = dst.pop('if', None)
            redirect = dst.pop('redirect', None)
            try:
                match, target = head_body(dst)
                rewrite = kmvo(
                    'rewrite',
                    match,
                    target,
                    status_to_word(self.status),
                )
                if if_:
                    rewrite = Section(f'if ({if_})', rewrite)
                rewrites += [rewrite]
            except:
                if redirect == None:
                    raise
            if redirect:
                rewrites += [kmvo('return', self.status, redirect)]
        return Section(
            'server',
            server_name,
            listen,
            *rewrites,
        )

    def render_refract(self):
        if self.is_rewrite:
            return self.render_rewrite()
        return self.render_redirect()

    def render(self):
        return [
            self.render_http_to_https(),
            self.render_refract(),
        ]

    def validate(self):
        tests = []
        for test in self.tests:
            src, dst = head_body(test)
            given = urlparse(src)
            expect = urlparse(dst)
            results = follow_hops(given, expect)
            test.update(results=results)
            tests += [test]
        return self.json(tests=tests)


    __repr__ = __repr__

def load_refract(spec):
    dst = spec.pop('dst', None)
    src = spec.pop('src', None)
    nginx = spec.pop('nginx', None)
    tests = spec.pop('tests', None)
    status = spec.pop('status', 301)
    if len(spec) == 1:
        dst, src = list(spec.items())[0]
    srcs = listify(src)
    return dict(dst=dst, srcs=srcs, nginx=nginx, tests=tests, status=status)

def load_refractr(config=None, refractr_pns=None, **kwargs):
    if refractr_pns == None:
        refractr_pns = ["*"]
    spec = yaml.safe_load(open(config))
    refracts = [load_refract(refract) for refract in spec['refracts']]
    spec['refracts'] = [refract for refract in refracts if fuzzy(refract['srcs']).include(*refractr_pns)]
    return RefractrConfig(spec)
