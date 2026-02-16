# RxQL Specification

**Version:** 0.1.0
**Status:** Draft

RxQL (Rx Query Language) is a context-aware query language for lazy loading in RxDjango. Unlike GraphQL, RxQL operates within an established context where the instance type and identity are already known, requiring only the specification of which fields to load.

---

## 1. Overview

### 1.1 Design Principles

1. **Context-Aware**: The target instance (type + ID) is known from the RxDjango channel or state tree
2. **Dual Syntax**: Supports both string syntax (concise) and object syntax (type-safe)
3. **Mixable**: String and object syntaxes can be combined at any nesting level
4. **Incremental**: Multiple loads accumulate; loading new fields preserves existing data
5. **Minimal**: Only specifies field shape, not instance identity

### 1.2 Comparison with GraphQL

| Aspect | GraphQL | RxQL |
|--------|---------|------|
| Instance specification | Required in query | Provided by context |
| Root selection | Query specifies root | Channel/instance provides root |
| Schema awareness | Client needs schema | Client has typed state tree |
| Typical query | `{ project(id: "123") { name } }` | `"{ name }"` |

---

## 2. Syntax

RxQL supports two interchangeable syntaxes that can be mixed freely.

### 2.1 String Syntax

Concise, GraphQL-inspired syntax for quick queries.

#### 2.1.1 Grammar (EBNF)

```ebnf
query         = "{" [ field_list ] "}" ;
field_list    = field { "," field } [ "," ] ;
field         = wildcard | named_field ;
wildcard      = "*" [ INTEGER ] | "**" ;
named_field   = IDENTIFIER [ query ] ;

IDENTIFIER    = ALPHA { ALPHA | DIGIT | "_" } ;
INTEGER       = DIGIT { DIGIT } ;
ALPHA         = "a"-"z" | "A"-"Z" | "_" ;
DIGIT         = "0"-"9" ;
```

#### 2.1.2 Whitespace

Whitespace (spaces, tabs, newlines) is ignored between tokens but not within tokens.

#### 2.1.3 Examples

```
{ name }                         // Single scalar field
{ name, description }            // Multiple scalar fields
{ name, owner { name, email } }  // Nested relation
{ tasks { title, status } }      // Array relation
{ * }                            // All scalar fields at this level
{ ** }                           // Full recursive expansion
{ *2 }                           // Expand 2 levels deep
{ *, owner { name } }            // All scalars + specific nested fields
```

### 2.2 Object Syntax

TypeScript/JavaScript object syntax for type safety and IDE autocompletion.

#### 2.2.1 Structure

```typescript
type RxQLQuery = {
  [fieldName: string]:
    | true                    // Include this scalar field
    | false                   // Explicitly exclude (optional)
    | RxQLQuery               // Nested object query
    | string                  // String sub-query (mixed mode)
  "*"?: true                  // All scalar fields
  "**"?: true                 // Full recursive expansion
  "*"?: number                // Expand N levels deep
}
```

#### 2.2.2 Examples

```typescript
// Single field
{ name: true }

// Multiple fields
{ name: true, description: true }

// Nested relation
{ name: true, owner: { name: true, email: true } }

// Array relation (applies to all items)
{ tasks: { title: true, status: true } }

// All scalars
{ "*": true }

// Full depth
{ "**": true }

// Depth limit
{ "*": 2 }

// All scalars + specific nested
{ "*": true, owner: { name: true } }
```

### 2.3 Mixed Syntax

String and object syntaxes can be combined at any level.

```typescript
// Object root with string sub-queries
{
  name: true,
  owner: "{ name, email }",
  tasks: {
    title: true,
    assignee: "{ * }"
  }
}

// Equivalent pure object form
{
  name: true,
  owner: { name: true, email: true },
  tasks: {
    title: true,
    assignee: { "*": true }
  }
}
```

---

## 3. Wildcards

### 3.1 Scalar Wildcard: `*`

Selects all scalar (non-relational) fields at the current level.

**String:** `{ * }`
**Object:** `{ "*": true }`

Scalar fields include:
- Primitive types (string, number, boolean)
- Date/time fields
- JSON fields
- Any field that doesn't reference another model

### 3.2 Recursive Wildcard: `**`

Expands the entire sub-tree with no depth limit.

**String:** `{ ** }`
**Object:** `{ "**": true }`

**Warning:** Use with caution on large state trees.

### 3.3 Depth-Limited Wildcard: `*N`

Expands N levels deep, including all fields at each level.

**String:** `{ *2 }`
**Object:** `{ "*": 2 }`

Depth counting:
- Level 0: Current instance's scalar fields
- Level 1: Direct relations (scalars only)
- Level 2: Relations of relations (scalars only)
- And so on...

**Example:** For a Project with Tasks with Assignees:
- `{ *0 }` = `{ * }` = Project scalars only
- `{ *1 }` = Project scalars + Task scalars (no Assignee)
- `{ *2 }` = Project scalars + Task scalars + Assignee scalars

### 3.4 Combining Wildcards with Explicit Fields

Explicit fields are merged with wildcards. Explicit nested queries override wildcard depth for that field.

```typescript
// All scalars at root, but go deeper on 'owner'
{ "*": true, owner: { "*": true, company: { name: true } } }

// Depth 1 everywhere, but depth 2 for tasks
{ "*": 1, tasks: { "*": 2 } }
```

---

## 4. Field Resolution

### 4.1 Duplicate Fields

When the same field appears multiple times (possible in merged queries), the most expansive query wins.

```
{ name, owner { name }, owner { email } }
// Resolves to: { name, owner { name, email } }
```

### 4.2 Wildcard + Explicit Merge

Explicit field queries are merged with wildcard expansions.

```
{ *, owner { name } }
// Result: all scalars at root + owner.name
// owner is NOT fully expanded, only 'name' is loaded
```

### 4.3 Unknown Fields

Querying a field that doesn't exist in the serializer results in an error response.

### 4.4 Non-Nestable Fields

Attempting to nest into a scalar field is an error.

```
{ name { foo } }  // Error: 'name' is not a relation
```

---

## 5. API Surface

### 5.1 Channel-Level Loading

```typescript
// Load anchor instance(s)
await channel.load(query: RxQLQuery | string): Promise<void>
```

For `many=True` channels, loads all visible anchor instances.

### 5.2 Instance-Level Loading

```typescript
// Load specific instance within state tree
await instance.load(query: RxQLQuery | string): Promise<void>

// Examples:
await state.load("{ name, tasks { title } }");
await state.owner.load("{ email, avatar }");
await state.tasks[0].load("{ assignee { * } }");
```

### 5.3 Batch Loading

Load multiple instances in a single round-trip:

```typescript
await channel.loadBatch([
  { instance: state.tasks[0], query: "{ assignee { * } }" },
  { instance: state.tasks[1], query: "{ assignee { * } }" },
]);
```

### 5.4 Return Value

`load()` returns a Promise that resolves when:
1. The query has been sent to the server
2. The response has been received
3. The state has been updated

The state tree is updated reactively; React components re-render automatically.

---

## 6. State Representation

### 6.1 Unloaded State

Before loading, instances exist as unloaded references:

```typescript
interface UnloadedInstance {
  _rx: {
    loaded: false;
    loading: boolean;
    type: string;      // Serializer path
    id: number | string;
  };
  load(query: RxQLQuery | string): Promise<void>;
}
```

### 6.2 Partially Loaded State

After loading some fields:

```typescript
interface PartiallyLoadedInstance {
  _rx: {
    loaded: true;
    loading: boolean;
    type: string;
    id: number | string;
    fields: Set<string>;  // Which fields are loaded
  };
  name: string;           // Loaded field
  description: string;    // Loaded field
  owner: UnloadedInstance; // Not yet loaded
  load(query: RxQLQuery | string): Promise<void>;
}
```

### 6.3 Accessing Unloaded Fields

Accessing a field that hasn't been loaded:
- Returns `undefined`
- In development mode, logs a warning with the field name
- Does NOT automatically trigger a load (explicit is better than implicit)

```typescript
// Development warning:
// "Accessing unloaded field 'owner' on Project#123. Call .load() first."
console.log(state.owner);  // undefined + warning
```

### 6.4 Array State

Unloaded arrays are empty arrays with metadata:

```typescript
interface UnloadedArray extends Array<never> {
  _rx: {
    loaded: false;
    loading: boolean;
    type: string;
  };
  load(query: RxQLQuery | string): Promise<void>;
}
```

After loading:

```typescript
interface LoadedArray<T> extends Array<T> {
  _rx: {
    loaded: true;
    loading: boolean;
    type: string;
    count: number;
  };
  load(query: RxQLQuery | string): Promise<void>;
}
```

---

## 7. Wire Protocol

### 7.1 Load Request

Client → Server:

```json
{
  "type": "rxql.load",
  "request_id": "uuid-v4",
  "loads": [
    {
      "instance_type": "myapp.serializers.ProjectSerializer",
      "instance_id": 123,
      "query": "{ name, tasks { title } }"
    }
  ]
}
```

The `query` field is always serialized as a string (object syntax is converted to string before sending).

### 7.2 Load Response

Server → Client:

```json
{
  "type": "rxql.data",
  "request_id": "uuid-v4",
  "instances": [
    {
      "_type": "myapp.serializers.ProjectSerializer",
      "_id": 123,
      "_fields": ["name"],
      "name": "My Project"
    },
    {
      "_type": "myapp.serializers.TaskSerializer",
      "_id": 456,
      "_fields": ["title"],
      "_parent": { "_type": "...", "_id": 123, "_field": "tasks" },
      "title": "First Task"
    }
  ]
}
```

### 7.3 Error Response

```json
{
  "type": "rxql.error",
  "request_id": "uuid-v4",
  "error": {
    "code": "INVALID_FIELD",
    "message": "Field 'unknown_field' does not exist on ProjectSerializer",
    "field": "unknown_field"
  }
}
```

### 7.4 Error Codes

| Code | Description |
|------|-------------|
| `PARSE_ERROR` | Query syntax is invalid |
| `INVALID_FIELD` | Field doesn't exist on serializer |
| `NOT_NESTABLE` | Attempted to nest into a scalar field |
| `PERMISSION_DENIED` | User can't access this instance |
| `NOT_FOUND` | Instance doesn't exist |

---

## 8. Incremental Loading

### 8.1 Accumulation

Multiple `load()` calls accumulate fields:

```typescript
await state.load("{ name }");
console.log(state.name);        // "My Project"
console.log(state.description); // undefined

await state.load("{ description }");
console.log(state.name);        // "My Project" (preserved)
console.log(state.description); // "A great project"
```

### 8.2 Refreshing

Re-loading an already-loaded field fetches fresh data:

```typescript
await state.load("{ name }");    // Fetches name
await state.load("{ name }");    // Fetches name again (refresh)
```

### 8.3 Tracking Loaded Fields

The `_rx.fields` set tracks which fields have been loaded:

```typescript
console.log(state._rx.fields);  // Set { "name", "description" }
```

---

## 9. TypeScript Integration

### 9.1 Generated Query Types

For each serializer, generate a corresponding query type:

```typescript
// Generated from ProjectSerializer
interface ProjectQuery {
  name?: true;
  description?: true;
  owner?: true | UserQuery | string;
  tasks?: true | TaskQuery | string;
  "*"?: true | number;
  "**"?: true;
}

// Usage with full type safety
const query: ProjectQuery = {
  name: true,
  owner: { name: true, email: true }
};

await project.load(query);
```

### 9.2 State Types

Generated state types reflect loaded/unloaded status:

```typescript
// Full type (all fields potentially loaded)
interface Project {
  _rx: RxMeta;
  name?: string;
  description?: string;
  owner?: User | UnloadedRef<User>;
  tasks?: Task[] | UnloadedArray<Task>;
  load(query: ProjectQuery | string): Promise<void>;
}
```

### 9.3 Type Guards

Helper functions to check load status:

```typescript
import { isLoaded, isUnloaded } from '@rxdjango/react';

if (isLoaded(state.owner)) {
  console.log(state.owner.name);  // Type-safe access
}

if (isUnloaded(state.tasks)) {
  await state.tasks.load("{ title }");
}
```

---

## 10. Backend Implementation

### 10.1 Query Parsing

The Django backend parses RxQL queries into field specifications:

```python
from rxdjango.rxql import parse_query

query = parse_query("{ name, owner { email } }")
# Result: {
#   'name': True,
#   'owner': {'email': True}
# }
```

### 10.2 Selective Serialization

The serializer uses the parsed query to determine which fields to include:

```python
class RxQLSerializer:
    def to_representation(self, instance, query=None):
        if query is None:
            return super().to_representation(instance)

        result = {'_type': self.type_path, '_id': instance.pk, '_fields': []}
        for field_name, sub_query in query.items():
            if field_name in self.fields:
                result['_fields'].append(field_name)
                result[field_name] = self.serialize_field(
                    instance, field_name, sub_query
                )
        return result
```

### 10.3 Channel Integration

The `StateConsumer` handles RxQL load requests:

```python
class StateConsumer(AsyncWebsocketConsumer):
    async def handle_rxql_load(self, request_id, loads):
        results = []
        for load in loads:
            instance = await self.get_instance(
                load['instance_type'],
                load['instance_id']
            )
            if await self.check_permission(instance):
                query = parse_query(load['query'])
                data = await self.serialize_with_query(instance, query)
                results.extend(data)

        await self.send_json({
            'type': 'rxql.data',
            'request_id': request_id,
            'instances': results
        })
```

---

## 11. Examples

### 11.1 Basic Loading

```typescript
// Connect to lazy channel
const channel = new ProjectChannel(projectId);
await channel.connect();

// State exists but is unloaded
console.log(channel.state._rx.loaded);  // false

// Load basic fields
await channel.state.load("{ name, description }");
console.log(channel.state.name);  // "My Project"
```

### 11.2 Nested Loading

```typescript
// Load project with owner
await channel.state.load(`{
  name,
  owner {
    name,
    email,
    avatar
  }
}`);

console.log(channel.state.owner.name);  // "John Doe"
```

### 11.3 Array Loading

```typescript
// Load all tasks with their titles
await channel.state.load("{ tasks { title, status } }");

channel.state.tasks.forEach(task => {
  console.log(task.title);  // Loaded
  console.log(task.assignee);  // Still unloaded
});
```

### 11.4 Deep Loading on Demand

```typescript
// User clicks on a task to expand it
async function onTaskClick(index: number) {
  const task = channel.state.tasks[index];

  await task.load(`{
    *,
    assignee { name, avatar },
    comments { text, author { name } }
  }`);
}
```

### 11.5 Mixed Syntax in React Component

```typescript
function ProjectView() {
  const [state, { load, loading }] = useChannelState(channel);

  useEffect(() => {
    load({
      name: true,
      description: true,
      owner: "{ name, avatar }",
      tasks: {
        "*": true,
        assignee: "{ name }"
      }
    });
  }, []);

  if (loading) return <Spinner />;

  return (
    <div>
      <h1>{state.name}</h1>
      <p>{state.description}</p>
      <Avatar user={state.owner} />
      <TaskList tasks={state.tasks} />
    </div>
  );
}
```

---

## 12. Future Considerations

### 12.1 Array Slicing (v2)

```
{ tasks[0:10] { title } }    // First 10 tasks
{ tasks[-5:] { title } }     // Last 5 tasks
```

### 12.2 Filtering (v2)

```
{ tasks(status: "open") { title } }
{ tasks(assignee__id: $userId) { title } }
```

### 12.3 Sorting (v2)

```
{ tasks(order_by: "-created_at") { title } }
```

### 12.4 Pagination (v2)

```typescript
await state.tasks.loadMore({
  query: "{ title }",
  limit: 20,
  offset: state.tasks.length
});
```

### 12.5 Subscriptions (v2)

Automatically keep certain queries up-to-date:

```typescript
channel.subscribe("{ tasks { title, status } }");
// Any changes to tasks automatically refresh these fields
```

---

## 13. Glossary

| Term | Definition |
|------|------------|
| **Anchor** | The root instance(s) of a ContextChannel |
| **Context** | The known instance type and ID from the channel/state tree |
| **Lazy Channel** | A channel with `lazy=True` that doesn't auto-load state |
| **Query** | An RxQL specification of which fields to load |
| **Scalar Field** | A field containing a primitive value (not a relation) |
| **State Tree** | The nested structure of instances in a channel |
| **Unloaded** | An instance/field that exists but hasn't been fetched |

---

## Appendix A: Full Grammar

```ebnf
(* RxQL String Syntax Grammar *)

query         = "{" , [ field_list ] , "}" ;
field_list    = field , { "," , field } , [ "," ] ;
field         = wildcard | named_field ;
wildcard      = ( "*" , [ integer ] ) | "**" ;
named_field   = identifier , [ query ] ;

identifier    = alpha , { alpha | digit | "_" } ;
integer       = digit , { digit } ;
alpha         = "a" | "b" | ... | "z" | "A" | "B" | ... | "Z" | "_" ;
digit         = "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" ;

(* Whitespace is ignored between tokens *)
```

## Appendix B: Object Syntax TypeScript Definition

```typescript
type RxQLScalar = true | false;
type RxQLWildcard = { "*"?: true | number; "**"?: true };
type RxQLNested = RxQLQuery | string;

type RxQLQuery = RxQLWildcard & {
  [field: string]: RxQLScalar | RxQLNested;
};

// Helper type for generated query interfaces
type FieldQuery<T> = true | T | string;
```
