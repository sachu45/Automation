import { Link } from "react-router-dom";
import { Button, Card, EmptyState } from "@/components/ui";

export function NotFoundPage() {
  return (
    <Card>
      <EmptyState
        title="Page not found"
        description="This page doesn't exist."
        action={
          <Link to="/">
            <Button variant="primary">Go to dashboard</Button>
          </Link>
        }
      />
    </Card>
  );
}
