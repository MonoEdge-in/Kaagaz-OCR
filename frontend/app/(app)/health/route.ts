export const dynamic = "force-dynamic"; // answer live on each request, never a build-time copy

export function GET() {
  return Response.json({ status: "ok" });
}
