"""Allow only supported Sandbox placements supplied by the browser latency check."""
# https://vercel.com/docs/sandbox/concepts/regions
REGIONS = frozenset({
    "arn1", "bom1", "cdg1", "cle1", "cpt1", "dub1", "fra1", "gru1", "hkg1",
    "hnd1", "iad1", "icn1", "kix1", "lhr1", "pdx1", "sfo1", "sin1", "syd1", "yul1",
})
DEFAULT_REGION = "iad1"


def validated_region(region):
    return region if region in REGIONS else DEFAULT_REGION
