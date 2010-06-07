# -*- coding: utf-8 -*-
import base64
import pickle
import random

from collections import deque
from copy import copy
from itertools import combinations

from django.db.models import get_models
from django.db.models.fields.related import (ForeignKey, OneToOneField,
                                             ManyToManyField)
from django.core.exceptions import SuspiciousOperation
from django.conf import settings
from django.utils.hashcompat import md5_constructor
from django.utils.importlib import import_module
from django.utils.simplejson import dumps

try:
    from django.db.models.fields.generic import GenericRelation
except ImportError:
    from django.contrib.contenttypes.generic import GenericRelation

try:
    qbe_formats = getattr(settings, "QBE_FORMATS_EXPORT", "qbe_formats")
    formats = import_module(qbe_formats).formats
except ImportError:
    from django_qbe.exports import formats
formats # Makes pyflakes happy


def qbe_models(admin_site=None, only_admin_models=False, json=False):
    app_models = get_models(include_auto_created=True, include_deferred=True)
    app_models_with_no_includes = get_models(include_auto_created=False,
                                             include_deferred=False)
    if admin_site:
        admin_models = [m for m, a in admin_site._registry.items()]
    else:
        admin_models = []
    if only_admin_models:
        app_models = admin_models
    graphs = {}

    def get_field_attributes(field):
        return {field.name: {
            'name': field.name,
            'type': type(field).__name__,
            'blank': field.blank,
            'label': u"%s" % field.verbose_name.capitalize(),
        }}

    def get_through_fields(field):
        # Deprecated
        through_fields = []
        for through_field in field.rel.through._meta.fields:
            label = through_field.verbose_name.capitalize()
            through_fields_dic = {
                'name': through_field.name,
                'type': type(through_field).__name__,
                'blank': through_field.blank,
                'label': u"%s" % label,
            }
            if hasattr(through_field.rel, "to"):
                through_rel = through_field.rel
                through_mod = through_rel.to.__module__.split(".")[-2]
                through_name = through_mod.capitalize()
                through_target = {
                    'name': through_name,
                    'model': through_rel.to.__name__,
                    'field': through_rel.get_related_field().name,
                }
                through_fields_dic.update({
                    "target": through_target,
                })
            through_fields.append(through_fields_dic)
        return through_fields

    def get_target(field):
        target = {
            'name': field.rel.to.__module__.split(".")[-2].capitalize(),
            'model': field.rel.to.__name__,
            'field': field.rel.to._meta.pk.name,
        }
        if hasattr(field.rel, 'through'):
            name = field.rel.through.__module__.split(".")[-2].capitalize()
            target.update({
                'through': {
                    'name': name,
                    'model': field.rel.through.__name__,
                    'field': field.rel.through._meta.pk.name,
                }
            })
        return target

    def get_target_relation(field, extras=""):
        target = get_target(field)
        relation = {
            'target': target,
            'type': type(field).__name__,
            'source': field.name,
            'arrows': extras,
        }
        return target, relation

    def add_relation(model, field, extras=""):
        target, relation = get_target_relation(field, extras=extras)
        if relation not in model['relations']:
            model['relations'].append(relation)
        model['fields'][field.name].update({'target': target})
        return model

    for app_model in app_models:
        model = {
            'name': app_model.__name__,
            'fields': {},
            'relations': [],
            'collapse': ((app_model not in admin_models) or
                         (app_model not in app_models_with_no_includes)),
        }

        for field in app_model._meta.fields:
            field_attributes = get_field_attributes(field)
            model['fields'].update(field_attributes)
            if isinstance(field, ForeignKey):
                model = add_relation(model, field)
            elif isinstance(field, OneToOneField):
                extras = "" # "[arrowhead=none arrowtail=none]"
                model = add_relation(model, field, extras=extras)

        if app_model._meta.many_to_many:
            for field in app_model._meta.many_to_many:
                field_attributes = get_field_attributes(field)
                model['fields'].update(field_attributes)
                if isinstance(field, ManyToManyField):
                    extras = "" # "[arrowhead=normal arrowtail=normal]"
                    model = add_relation(model, field, extras=extras)
                elif isinstance(field, GenericRelation):
                    extras = "" # '[style="dotted"]
                                # [arrowhead=normal arrowtail=normal]'
                    model = add_relation(model, field, extras=extras)

        app_title = app_model._meta.app_label.title()
        if app_title not in graphs:
            graphs[app_title] = {}
        graphs[app_title].update({app_model.__name__: model})

    if json:
        return dumps(graphs)
    else:
        return graphs


def qbe_graph(admin_site=None, directed=False):
    models = qbe_models(admin_site)
    graph = {}
    for k, v in models.items():
        for l, w in v.items():
            key = "%s.%s" % (k, l)
            if key not in graph:
                graph[key] = []
            for relation in w['relations']:
                source = relation['source']
                target = relation['target']
                if "through" in target:
                    through = target["through"]
                    model = "%s.%s" % (through['name'], through['model'])
                    value = (source, model, through['field'])
                else:
                    model = "%s.%s" % (target['name'], target['model'])
                    value = (source, model, target['field'])
                if value not in graph[key]:
                    graph[key].append(value)
                if not directed:
                    if model not in graph:
                        graph[model] = []
                    target_field = target['field']
                    target_value = (target_field, key, source)
                    if target_value not in graph[model]:
                        graph[model].append(target_value)
            if not graph[key]:
                del graph[key]
    return graph


def qbe_tree(graph, nodes, root=None):
    """
    Given a graph, nodes to explore and an optinal root, do a breadth-first
    search in order to return the tree.
    """
    if root:
        start = root
    else:
        index = random.randint(0, len(nodes) - 1)
        start = nodes[index]
    # A queue to BFS instead DFS
    to_visit = deque()
    cnodes = copy(nodes)
    visited = set()
    # Format is (parent, parent_edge, neighbor, neighbor_field)
    to_visit.append((None, None, start, None))
    tree = {}
    while len(to_visit) != 0 and nodes:
        parent, parent_edge, v, v_edge = to_visit.pop()
        # Prune
        if v in nodes:
            nodes.remove(v)
        node = graph[v]
        if v not in visited and len(node) > 1:
            visited.add(v)
            # Preorder process
            if all((parent, parent_edge, v, v_edge)):
                if parent not in tree:
                    tree[parent] = []
                if (parent_edge, v, v_edge) not in tree[parent]:
                    tree[parent].append((parent_edge, v, v_edge))
                if v not in tree:
                    tree[v] = []
                if (v_edge, parent, parent_edge) not in tree[v]:
                    tree[v].append((v_edge, parent, parent_edge))
            # Iteration
            for node_edge, neighbor, neighbor_edge in node:
                value = (v, node_edge, neighbor, neighbor_edge)
                to_visit.append(value)
    remove_leafs(tree, cnodes)
    return tree, (len(nodes) == 0)


def remove_leafs(tree, nodes):

    def get_leafs(tree, nodes):
        return [node for node, edges in tree.items()
                     if len(edges) < 2 and node not in nodes]

    def delete_edge_leafs(tree, leaf):
        for node, edges in tree.items():
            for node_edge, neighbor, neighbor_edge in edges:
                if leaf == neighbor:
                    edge = (node_edge, neighbor, neighbor_edge)
                    tree[node].remove(edge)
        del tree[leaf]

    leafs = get_leafs(tree, nodes)
    iterations = 0
    while leafs or iterations > len(tree) ^ 2:
        for node in leafs:
            if node in tree:
                delete_edge_leafs(tree, node)
        leafs = get_leafs(tree, nodes)
        iterations += 0
    return tree


def qbe_forest(graph, nodes):
    forest = []
    for node, edges in graph.items():
        tree, are_all = qbe_tree(graph, copy(nodes), root=node)
        if are_all and tree not in forest:
            forest.append(tree)
    return sorted(forest, cmp=lambda x, y: cmp(len(x), len(y)))


def find_all_paths(graph, start, end, path=[]):
    path = path + [start]
    if start == end:
        return [path]
    if start not in graph:
        return []
    paths = []
    for source_field, (node, target_field) in graph[start].items():
        if node not in path:
            newpaths = find_all_paths(graph, node, end, path)
            for newpath in newpaths:
                paths.append(newpath)
    return paths


def autocomplete_graph(admin_site, current_models, directed=False):
    if len(current_models) < 2:
        return None
    graph = qbe_graph(admin_site, directed=directed)
    valid_paths = []
    for c, d in combinations(current_models, 2):
        paths = find_all_paths(graph, c, d)
        for path in paths:
            if all(map(lambda x: x in path, current_models)):
                for model in current_models:
                    path.remove(model)
                if path not in valid_paths:
                    valid_paths.append(path)
    return sorted(valid_paths, cmp=lambda x, y: cmp(len(x), len(y)))


# Taken from django.contrib.sessions.backends.base
def pickle_encode(session_dict):
    "Returns the given session dictionary pickled and encoded as a string."
    pickled = pickle.dumps(session_dict, pickle.HIGHEST_PROTOCOL)
    pickled_md5 = md5_constructor(pickled + settings.SECRET_KEY).hexdigest()
    return base64.encodestring(pickled + pickled_md5)


# Adapted from django.contrib.sessions.backends.base
def pickle_decode(session_data):
    # The '+' character is translated to ' ' in request
    session_data = session_data.replace(" ", "+")
    # The length of the encoded string should be a multiple of 4
    while (((len(session_data) / 4.0) - (len(session_data) / 4)) != 0):
        session_data += u"="
    encoded_data = base64.decodestring(session_data)
    pickled, tamper_check = encoded_data[:-32], encoded_data[-32:]
    pickled_md5 = md5_constructor(pickled + settings.SECRET_KEY).hexdigest()
    if pickled_md5 != tamper_check:
        raise SuspiciousOperation("User tampered with session cookie.")
    try:
        return pickle.loads(pickled)
    # Unpickling can cause a variety of exceptions. If something happens,
    # just return an empty dictionary (an empty session).
    except:
        return {}
