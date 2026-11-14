"""Compact reference queries over a fresh Studio project export."""
import hashlib
import re
from collections import Counter, defaultdict, deque

TOKEN = re.compile(
    r"--\[(?P<comment>=*)\[.*?\](?P=comment)\]|--[^\n]*|"
    r"\[(?P<long>=*)\[.*?\](?P=long)\]|"
    r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|[A-Za-z_]\w*|[^\s]''',
    re.DOTALL,
)
IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


def literal(token):
    if len(token) >= 2 and token[0] in "\"'" and "\\" not in token:
        return token[1:-1]
    return None


def expression(tokens, offset, script):
    """Resolve literal game/script chains. Aliases and dynamic expressions stay unknown."""
    base = tokens[offset:offset + 1]
    if base not in (["game"], ["script"]):
        return None, offset
    path = list(script) if base == ["script"] else []
    offset += 1
    while offset < len(tokens):
        tail = tokens[offset:offset + 5]
        if len(tail) >= 2 and tail[0] == "." and IDENTIFIER.fullmatch(tail[1]):
            path = path[:-1] if tail[1] == "Parent" else path + [tail[1]]
            offset += 2
        elif (len(tail) == 5 and tail[0] == ":" and tail[1] in
              ("GetService", "WaitForChild", "FindFirstChild") and tail[2] == "("
              and tail[4] == ")" and literal(tail[3]) is not None):
            path.append(literal(tail[3]))
            offset += 5
        elif len(tail) >= 3 and tail[0] == "[" and tail[2] == "]" and literal(tail[1]) is not None:
            path.append(literal(tail[1]))
            offset += 3
        else:
            break
    return tuple(path), offset


def impact(snapshot, target, limit, depth, include_source=False):
    counts = Counter()
    scripts = []
    pending = [(snapshot["tree"], ())]
    while pending:
        node, path = pending.pop()
        counts[path] += 1
        if "source" in node:
            scripts.append((path, node["source"]))
        for child in node.get("children", []):
            pending.append((child, path + (child.get("actualName", child["name"]),)))
    if not counts[target]:
        raise ValueError("Target path not found in Studio export")
    if counts[target] != 1:
        raise ValueError("Target path is ambiguous in Studio export")
    target_source = None
    if include_source:
        target_source = next((source for path, source in scripts if path == target), None)
        if target_source is None:
            raise ValueError("Source requested for a target that is not a script")

    reverse = defaultdict(set)
    direct, possible = [], []
    direct_count = possible_count = unresolved = 0
    target_name = target[-1]
    for path, source in scripts:
        # These four spellings are the only entry points into the token scan.
        # A relative expression can resolve without spelling the target name.
        if not any(word in source for word in ("require", "game", "script", target_name)):
            continue
        lines = None
        matches = [m for m in TOKEN.finditer(source) if not m[0].startswith("--")]
        tokens = [m[0] for m in matches]
        line, previous = 1, 0
        for index, token in enumerate(tokens):
            is_chain = token in ("game", "script")
            is_possible = token == target_name or literal(token) == target_name
            if not is_chain and not is_possible and token != "require":
                continue
            position = matches[index].start()
            line += source.count("\n", previous, position)
            previous = position
            if token == "require":
                dest, end = expression(tokens, index + 2, path)
                if (tokens[index + 1:index + 2] == ["("] and tokens[end:end + 1] == [")"]
                        and dest is not None and counts[dest] == 1 and counts[path] == 1):
                    reverse[dest].add(path)
                else:
                    unresolved += 1
            dest, _ = expression(tokens, index, path) if is_chain else (None, index)
            if dest == target and counts[path] == 1:
                direct_count += 1
                if len(direct) < limit:
                    if lines is None:
                        lines = source.splitlines()
                    direct.append({"path":path, "line":line, "excerpt":lines[line - 1].strip()[:200]})
            elif is_possible:
                possible_count += 1
                if len(possible) < limit:
                    if lines is None:
                        lines = source.splitlines()
                    possible.append({"path":path, "line":line, "excerpt":lines[line - 1].strip()[:200]})
    visited = {target}
    queue = deque([(target, 0)])
    dependent = []
    while queue:
        path, distance = queue.popleft()
        if distance == depth:
            continue
        for item in sorted(reverse[path] - visited):
            visited.add(item)
            queue.append((item, distance + 1))
            if len(dependent) < limit:
                dependent.append({"path":item, "hops":distance + 1})
    result = {
        "target": target, "captured_at": snapshot.get("captured_at"), "scripts_scanned":len(scripts),
        "literal_references":direct, "possible_name_matches":possible,
        "require_dependents":dependent, "unresolved_requires":unresolved,
        "counts":{"literal":direct_count, "possible":possible_count, "dependents":len(visited)-1},
        "note":"Static token scan. Aliases, shadowing, dynamic references and runtime connections require inspection.",
    }
    if include_source:
        result.update(source=target_source, hash=hashlib.sha256(target_source.encode("utf-8")).hexdigest())
    return result
