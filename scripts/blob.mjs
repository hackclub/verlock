import { get, put, del, BlobPreconditionFailedError } from '@vercel/blob';
let input = '';
for await (const chunk of process.stdin) input += chunk;
const {operation, pathname, value, etag} = JSON.parse(input);
if (operation === 'get' || operation === 'get_version') {
  const result = await get(pathname, {access:'private', useCache:false});
  const data = result ? JSON.parse(await new Response(result.stream).text()) : null;
  console.log(JSON.stringify(operation === 'get' ? data : {value:data, etag:result?.blob.etag ?? null}));
} else if (operation === 'compare_put') {
  try {
    await put(pathname, JSON.stringify(value), {access:'private', addRandomSuffix:false, contentType:'application/json', cacheControlMaxAge:60, ...(etag ? {ifMatch:etag} : {allowOverwrite:false})});
    console.log('true');
  } catch (error) {
    if (error instanceof BlobPreconditionFailedError || (!etag && /already exists/i.test(error.message))) console.log('false');
    else throw error;
  }
} else if (operation === 'put') {
  await put(pathname, JSON.stringify(value), {access:'private', addRandomSuffix:false, allowOverwrite:true, contentType:'application/json', cacheControlMaxAge:60});
  console.log('null');
} else if (operation === 'delete') {
  await del(pathname);
  console.log('null');
} else throw new Error('Unknown operation');
