"""Literal URL membership, bounded search and traceable CSV loading."""
import csv
import hashlib
import itertools
import math
from collections import Counter


def cluster_key(urls):
    return hashlib.sha256('\n'.join(sorted(urls)).encode('utf-8')).hexdigest()[:16]


def csv_rows(path):
    with open(path, encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def load_items(path):
    items = {}
    for number, row in enumerate(csv_rows(path), 2):
        item = dict(source_id=row['source_id'], url=row['url'], title=row.get('title', ''),
                    body=row.get('body', ''), published_at=row.get('published_at_utc') or None,
                    date_only=row.get('date_only', '') in ('1', 'true', 'True'),
                    lang=row.get('lang', ''), raw_hash='unavailable:csv-export')
        if not item['url'] or not item['source_id']:
            raise ValueError('empty URL/source')
        old = items.get(item['url'])
        if old and any(old[k] != v for k, v in item.items()):
            raise ValueError('conflicting duplicate URL: ' + item['url'])
        if old:
            old['_csv_rows'].append(number)
        else:
            item['_csv_rows'] = [number]
            items[item['url']] = item
    return items


def load_clusters(path):
    result = {}
    for number, row in enumerate(csv_rows(path), 2):
        n = int(row['n_members'])
        if n <= 0:
            raise ValueError('cardinality must be positive')
        c = dict(cluster_key=row['cluster_key'], n_members=n,
                 sources=sorted(set(filter(None, row.get('sources', '').split('|')))),
                 fate=row.get('fate', ''))
        old = result.get(c['cluster_key'])
        if old and any(old[k] != v for k, v in c.items()):
            raise ValueError('conflicting duplicate cluster key')
        if old:
            old['csv_rows'].append(number)
        else:
            c['csv_rows'] = [number]
            result[c['cluster_key']] = c
    return list(result.values())


def valid_members(c, urls, items):
    return (len(urls) == len(set(urls)) == c['n_members']
            and all(u in items for u in urls) and cluster_key(urls) == c['cluster_key']
            and (not c['sources'] or {items[u]['source_id'] for u in urls} == set(c['sources'])))


def recover(clusters, items, combination_budget=1_000_000):
    if type(combination_budget) is not int or combination_budget < 1:
        raise ValueError('invalid combination budget')
    singleton = {cluster_key([u]): u for u in items}
    recovered, missing, used, keys = {}, [], set(), set()
    for c in clusters:
        key, n = c['cluster_key'], c['n_members']
        if key in keys or type(n) is not int or n < 1:
            raise ValueError('duplicate key or invalid cardinality')
        keys.add(key)
        pool = sorted(u for u in items if not c['sources'] or items[u]['source_id'] in c['sources'])
        found, why = None, 'no_exact_hash_match'
        if n == 1:
            hit = singleton.get(key)
            found = [hit] if hit and valid_members(c, [hit], items) else None
        elif n >= 4:
            why = 'requires_ci_recluster'
        elif math.comb(len(pool), n) > combination_budget:
            why = 'search_budget_exceeded'
        else:
            found = next((list(v) for v in itertools.combinations(pool, n)
                          if valid_members(c, v, items)), None)
        if found:
            if used.intersection(found):
                raise ValueError('overlapping memberships within run')
            recovered[key] = found
            used.update(found)
        else:
            missing.append(dict(cluster_key=key, n_members=n, reason=why))
    return recovered, missing


def report_run(run_key, clusters, items, budget=1_000_000):
    recovered, missing = recover(clusters, items, budget)
    memberships = [dict(c, urls=recovered.get(c['cluster_key'], []),
                        method=('hash' if c['cluster_key'] in recovered else 'unresolved'),
                        source_match=('exact' if c['sources'] else 'unverified_all_run_search')) for c in clusters]
    total = sum(c['n_members'] for c in clusters)
    matched = sum(map(len, recovered.values()))
    return dict(run_key=run_key, memberships=memberships, items=len(items), clusters=len(clusters),
                input_csv_rows=sum(len(i['_csv_rows']) for i in items.values()),
                chosen_csv_rows=sum(len(c.get('csv_rows', [1])) for c in clusters),
                recovered_count=len(recovered), unrecovered_count=len(missing),
                unrecovered_clusters=missing, cluster_coverage=len(recovered)/len(clusters) if clusters else 0,
                member_coverage=matched/total if total else 0,
                expected_members=total, recovered_members=matched,
                unassigned_urls=sorted(set(items)-set(itertools.chain.from_iterable(recovered.values()))),
                cardinality_discrepancy=total-len(items),
                recovery_by_n=dict(Counter(c['n_members'] for c in clusters if c['cluster_key'] in recovered)))
