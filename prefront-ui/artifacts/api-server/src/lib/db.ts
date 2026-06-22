// Re-export the shared db instance from the workspace db lib.
// The db lib owns the pool; we just consume it here.
export { db } from "@workspace/db";
