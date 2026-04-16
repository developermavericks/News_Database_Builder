import logging
import json
import os
from typing import Dict, Any, List
from pathlib import Path

logger = logging.getLogger("GRAPHIFY")

def graphify_article(text: str, title: str = "") -> Dict[str, Any]:
    """
    Converts article text into a dense Knowledge Map (nodes + edges).
    This drastically reduces the token volume required for final summarization.
    """
    if not text or len(text) < 100:
        return {"nodes": [], "edges": []}

    try:
        # We leverage the semantic extraction capability if available, 
        # otherwise we fallback to a high-density entity extractor.
        from scraper.llm import _call_ollama_blocking
        
        prompt = (
            f"Convert this news article into a Knowledge Graph (JSON format).\n"
            f"EXO-CORE extraction: Identify key Entities (Person, Organization, Event, Tech) and their Relations.\n"
            f"Output JSON with 'nodes' (id, label, type) and 'edges' (source, target, relation).\n\n"
            f"Goal: Condense {len(text)} characters into a dense semantic map to save tokens.\n\n"
            f"Article Title: {title}\n"
            f"Content: {text[:3000]}"
        )
        
        response = _call_ollama_blocking(prompt)
        
        from scraper.llm import safe_json_parse
        data = safe_json_parse(response)
        
        # Validation
        if "nodes" not in data: data["nodes"] = []
        if "edges" not in data: data["edges"] = []
        
        logger.info(f"GRAPHIFY: Mapped article '{title}' to {len(data['nodes'])} nodes and {len(data['edges'])} edges.")
        return data
        
    except Exception as e:
        logger.error(f"GRAPHIFY: Mapping failed for '{title}': {e}")
        return {"nodes": [], "edges": [], "error": str(e)}

def graph_to_context_string(graph: Dict[str, Any]) -> str:
    """
    Converts a graph dict into a dense string format for LLM context.
    Example: [Tech: AI] --advances--> [Field: Medicine]
    """
    if not graph or (not graph.get("nodes") and not graph.get("edges")):
        return ""
        
    lines = []
    node_map = {n["id"]: n["label"] for n in graph.get("nodes", []) if "id" in n and "label" in n}
    
    for edge in graph.get("edges", []):
        src = node_map.get(edge.get("source"), edge.get("source"))
        tgt = node_map.get(edge.get("target"), edge.get("target"))
        rel = edge.get("relation", "relates to")
        if src and tgt:
            lines.append(f"[{src}] --{rel}--> [{tgt}]")
            
    # Add isolated nodes if any
    covered_nodes = set()
    for edge in graph.get("edges", []):
        covered_nodes.add(edge.get("source"))
        covered_nodes.add(edge.get("target"))
        
    for nid, label in node_map.items():
        if nid not in covered_nodes:
            lines.append(f"Node: {label}")
            
    return "\n".join(lines)
