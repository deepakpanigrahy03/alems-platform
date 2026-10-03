# Store Repair and Corrections { #store-repair }

Two tools fix stored data; both write through the store's single writer and both default to showing a plan first.

## Duplicate rows (INV-D1)

Older stores may contain the same sample, interaction or event row twice. Schema version 117 adds unique indexes that refuse such duplicates, so migrations stop on a store that still has them.

```bash
cd <your sandbox>
alems validate persistence                         # report duplicates and orphans
alems validate persistence --repair --yes          # remove duplicates, backup first
alems validate persistence --repair --yes --no-backup   # test data only
alems validate persistence                         # confirm none remain
```

The repair keeps one copy per natural key (declared in core/validation/persistence_keys.yaml) and moves or deletes dependent rows as declared there.

## Registered corrections

Some historical values were stored by older code in a way now known to be wrong (for example 0 where a value was not measured). Registered corrections recompute them:

```bash
alems validate corrections            # plan: rows that would change
alems validate corrections --apply    # backup, then write
```

A correction is idempotent: a second plan shows zero changes.

## Orphan rows (INV-D2)

Rows that reference a run absent from the store are reported, not deleted. Their disposition is recorded per store at each gate.
