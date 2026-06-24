// Ambient declaration for `dagre` — the package ships no types and `@types/dagre`
// is not a workspace dependency. The graph code (DataGraph.tsx, SchemaDiagram.tsx)
// already narrows access via `(dagre as any).graphlib.Graph`, so a permissive
// default export is sufficient to satisfy the compiler under noImplicitAny.
declare module "dagre" {
  const dagre: any;
  export default dagre;
}
