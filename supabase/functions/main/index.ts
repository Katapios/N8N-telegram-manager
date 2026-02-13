Deno.serve(async () => {
  return new Response(JSON.stringify({ ok: true, service: "lazybones-functions" }), {
    headers: { "content-type": "application/json" },
  })
})
