// AWS regional endpoints approximate latency to the corresponding Sandbox region.
// These are HTTPS timings, not ICMP or measurements of a running desktop.
const sandboxProbeRegions = {
    arn1: 'eu-north-1', bom1: 'ap-south-1', cdg1: 'eu-west-3',
    cle1: 'us-east-2', cpt1: 'af-south-1', dub1: 'eu-west-1',
    fra1: 'eu-central-1', gru1: 'sa-east-1', hkg1: 'ap-east-1',
    hnd1: 'ap-northeast-1', iad1: 'us-east-1', icn1: 'ap-northeast-2',
    kix1: 'ap-northeast-3', lhr1: 'eu-west-2', pdx1: 'us-west-2',
    sfo1: 'us-west-1', sin1: 'ap-southeast-1', syd1: 'ap-southeast-2',
    yul1: 'ca-central-1',
};

async function findFastestSandboxRegion() {
    const results = await Promise.all(Object.entries(sandboxProbeRegions).map(async ([region, awsRegion]) => {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 1800);
        try {
            // A nonexistent bucket returns a tiny regional 404. The service root
            // can redirect to AWS's global website, which would invalidate timing.
            const url = `https://s3.${awsRegion}.amazonaws.com/verlock-ping-${crypto.randomUUID()}/`;
            const options = {
                method: 'HEAD', mode: 'no-cors', credentials: 'omit', cache: 'no-store',
                referrerPolicy: 'no-referrer', signal: controller.signal,
            };
            // Warm up DNS/TLS, then time a second tiny request on the connection.
            await fetch(url, options);
            const start = performance.now();
            await fetch(url, options);
            return {region, milliseconds: performance.now() - start};
        } catch {
            return null;
        } finally {
            clearTimeout(timeout);
        }
    }));
    return results.filter(Boolean).sort((a, b) => a.milliseconds - b.milliseconds)[0]?.region || 'iad1';
}
