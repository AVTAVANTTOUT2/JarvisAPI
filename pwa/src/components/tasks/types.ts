/** Types partages pour la page taches. */

export interface TaskItemRaw {
  id: number;
  title: string;
  description?: string | null;
  priority: 'high' | 'medium' | 'low';
  status: 'todo' | 'doing' | 'done';
  due_date: string | null;
  category: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface TasksResponse {
  tasks: TaskItemRaw[];
}
