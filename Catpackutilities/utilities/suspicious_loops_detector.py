#!/usr/bin/env python3
"""
Suspicious Loops Detector for Code Analysis
==========================================

Detects loops that may cause memory issues, performance problems, or CPU lags:
- Infinite loops or loops with no proper exit conditions
- Loops with excessive memory allocation
- Nested loops with high complexity
- Loops with I/O operations that could cause blocking
- Loops with recursive calls
- Loops with inefficient operations (string concatenation, etc.)
- Loops that may cause memory leaks

Author: Cat Pack Utilities
Version: 1.0
Date: 2025-10-01
"""

import os
import sys
import re
import glob
import zipfile
import json
import logging
import math
from datetime import datetime
from collections import defaultdict, Counter
from enum import Enum
from typing import Dict, List, Tuple, Optional, Set
import argparse

# Configure logging
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from logging_config import configure_logging
configure_logging()
logger = logging.getLogger(__name__)

class LoopSeverity(Enum):
    """Loop issue severity levels"""
    CRITICAL = "CRITICAL"  # Definite performance/memory issues
    HIGH = "HIGH"         # Very likely to cause problems
    MEDIUM = "MEDIUM"     # Potential performance impact
    LOW = "LOW"           # Minor inefficiencies
    INFO = "INFO"         # Optimization suggestions

class LoopCategory(Enum):
    """Categories of loop issues"""
    INFINITE_LOOP = "Infinite Loop"
    MEMORY_ALLOCATION = "Memory Allocation"
    NESTED_COMPLEXITY = "Nested Complexity"
    IO_OPERATIONS = "I/O Operations"
    RECURSIVE_CALLS = "Recursive Calls"
    INEFFICIENT_OPERATIONS = "Inefficient Operations"
    STRING_CONCATENATION = "String Concatenation"
    COLLECTION_OPERATIONS = "Collection Operations"
    BLOCKING_OPERATIONS = "Blocking Operations"
    RESOURCE_INTENSIVE = "Resource Intensive"

class SuspiciousLoopPattern:
    """Represents a suspicious loop pattern"""
    def __init__(self, pattern: str, severity: LoopSeverity, 
                 category: LoopCategory, description: str, 
                 recommendation: str = "", file_extensions: List[str] = None,
                 complexity_multiplier: float = 1.0):
        self.pattern = re.compile(pattern, re.IGNORECASE | re.MULTILINE | re.DOTALL)
        self.severity = severity
        self.category = category
        self.description = description
        self.recommendation = recommendation
        self.file_extensions = file_extensions or []
        self.complexity_multiplier = complexity_multiplier

class SuspiciousLoopsDetector:
    """Main suspicious loops detector class"""
    
    def __init__(self):
        self.patterns = self._initialize_patterns()
        self.results = defaultdict(list)
        self.stats = defaultdict(int)
        self.scanned_files = 0
        self.total_issues = 0
        
    def _initialize_patterns(self) -> List[SuspiciousLoopPattern]:
        """Initialize suspicious loop detection patterns"""
        patterns = []
        
        # Infinite Loops - Java/JavaScript/C/C++
        patterns.extend([
            SuspiciousLoopPattern(
                r'while\s*\(\s*true\s*\)\s*\{[^}]*(?!break|return|throw)[^}]*\}',
                LoopSeverity.CRITICAL,
                LoopCategory.INFINITE_LOOP,
                "Infinite while loop without break condition",
                "Add proper exit condition with break, return, or exception",
                ['.java', '.js', '.ts', '.cpp', '.c', '.cs'],
                3.0
            ),
            SuspiciousLoopPattern(
                r'for\s*\(\s*;\s*;\s*\)\s*\{[^}]*(?!break|return|throw)[^}]*\}',
                LoopSeverity.CRITICAL,
                LoopCategory.INFINITE_LOOP,
                "Infinite for loop without termination condition",
                "Add proper loop termination condition",
                ['.java', '.js', '.ts', '.cpp', '.c', '.cs'],
                3.0
            ),
            SuspiciousLoopPattern(
                r'do\s*\{[^}]*(?!break|return|throw)[^}]*\}\s*while\s*\(\s*true\s*\)',
                LoopSeverity.CRITICAL,
                LoopCategory.INFINITE_LOOP,
                "Infinite do-while loop",
                "Add proper exit condition",
                ['.java', '.js', '.ts', '.cpp', '.c', '.cs'],
                3.0
            ),
        ])
        
        # Infinite Loops - Python
        patterns.extend([
            SuspiciousLoopPattern(
                r'while\s+True\s*:[^:]*?(?:\n(?:\s{4,}|\t+)[^\n]*)*?(?!.*(?:break|return|raise))',
                LoopSeverity.CRITICAL,
                LoopCategory.INFINITE_LOOP,
                "Infinite Python while loop without break condition",
                "Add break, return, or raise statement for proper exit",
                ['.py'],
                3.0
            ),
            SuspiciousLoopPattern(
                r'for\s+\w+\s+in\s+itertools\.count\(\)[^:]*:[^:]*?(?:\n(?:\s{4,}|\t+)[^\n]*)*?(?!.*(?:break|return|raise))',
                LoopSeverity.CRITICAL,
                LoopCategory.INFINITE_LOOP,
                "Infinite iterator loop without break condition",
                "Add proper exit condition",
                ['.py'],
                3.0
            ),
        ])
        
        # Memory Allocation in Loops
        patterns.extend([
            SuspiciousLoopPattern(
                r'(?:while|for)\s*\([^)]*\)\s*\{[^}]*(?:new\s+\w+(?:\[\])?|ArrayList|HashMap|Vector|LinkedList)[^}]*\}',
                LoopSeverity.HIGH,
                LoopCategory.MEMORY_ALLOCATION,
                "Loop with memory allocation - potential memory leak",
                "Consider object reuse or move allocation outside loop",
                ['.java', '.cs'],
                2.0
            ),
            SuspiciousLoopPattern(
                r'(?:while|for)[^:]*:[^:]*?(?:\n(?:\s{4,}|\t+)[^\n]*)*?(?:list\(\)|dict\(\)|\[\]|\{\}|set\(\))',
                LoopSeverity.HIGH,
                LoopCategory.MEMORY_ALLOCATION,
                "Python loop with memory allocation",
                "Consider pre-allocating or reusing objects",
                ['.py'],
                2.0
            ),
            SuspiciousLoopPattern(
                r'(?:while|for)\s*\([^)]*\)\s*\{[^}]*(?:malloc|calloc|new\s+\w+\[)[^}]*\}',
                LoopSeverity.HIGH,
                LoopCategory.MEMORY_ALLOCATION,
                "Loop with dynamic memory allocation",
                "Consider memory pooling or pre-allocation",
                ['.cpp', '.c'],
                2.0
            ),
        ])
        
        # Nested Loops with High Complexity
        patterns.extend([
            SuspiciousLoopPattern(
                r'(?:while|for)\s*\([^)]*\)\s*\{[^{}]*(?:while|for)\s*\([^)]*\)\s*\{[^{}]*(?:while|for)\s*\([^)]*\)\s*\{',
                LoopSeverity.HIGH,
                LoopCategory.NESTED_COMPLEXITY,
                "Triple nested loop - O(n³) complexity",
                "Consider algorithm optimization or different data structures",
                ['.java', '.js', '.ts', '.cpp', '.c', '.cs'],
                3.0
            ),
            SuspiciousLoopPattern(
                r'(?:while|for)[^:]*:[^:]*?(?:\n(?:\s{4,}|\t+)[^\n]*)*?(?:while|for)[^:]*:[^:]*?(?:\n(?:\s{8,}|\t{2,})[^\n]*)*?(?:while|for)[^:]*:',
                LoopSeverity.HIGH,
                LoopCategory.NESTED_COMPLEXITY,
                "Triple nested Python loop - O(n³) complexity",
                "Consider using itertools, numpy, or algorithm optimization",
                ['.py'],
                3.0
            ),
            SuspiciousLoopPattern(
                r'(?:while|for)\s*\([^)]*\)\s*\{[^{}]*(?:while|for)\s*\([^)]*\)\s*\{[^{}]*(?:while|for)\s*\([^)]*\)\s*\{[^{}]*(?:while|for)\s*\([^)]*\)\s*\{',
                LoopSeverity.CRITICAL,
                LoopCategory.NESTED_COMPLEXITY,
                "Quadruple nested loop - O(n⁴) complexity",
                "Urgent need for algorithm redesign",
                ['.java', '.js', '.ts', '.cpp', '.c', '.cs'],
                4.0
            ),
        ])
        
        # I/O Operations in Loops
        patterns.extend([
            SuspiciousLoopPattern(
                r'(?:while|for)\s*\([^)]*\)\s*\{[^}]*(?:System\.out\.print|System\.in\.read|FileInputStream|FileOutputStream|Socket|HttpURLConnection)[^}]*\}',
                LoopSeverity.HIGH,
                LoopCategory.IO_OPERATIONS,
                "I/O operations inside loop - potential blocking",
                "Consider batching I/O operations or using async I/O",
                ['.java'],
                2.5
            ),
            SuspiciousLoopPattern(
                r'(?:while|for)[^:]*:[^:]*?(?:\n(?:\s{4,}|\t+)[^\n]*)*?(?:print\(|input\(|open\(|urllib|requests\.|socket\.)',
                LoopSeverity.HIGH,
                LoopCategory.IO_OPERATIONS,
                "I/O operations inside Python loop",
                "Consider batching operations or using async/await",
                ['.py'],
                2.5
            ),
            SuspiciousLoopPattern(
                r'(?:while|for)\s*\([^)]*\)\s*\{[^}]*(?:printf|scanf|fopen|fread|fwrite|cout|cin)[^}]*\}',
                LoopSeverity.MEDIUM,
                LoopCategory.IO_OPERATIONS,
                "I/O operations inside C/C++ loop",
                "Consider buffering or batching I/O operations",
                ['.cpp', '.c'],
                2.0
            ),
        ])
        
        # String Concatenation in Loops
        patterns.extend([
            SuspiciousLoopPattern(
                r'(?:while|for)\s*\([^)]*\)\s*\{[^}]*(?:String\s+\w+\s*\+=|StringBuilder.*append)[^}]*\}',
                LoopSeverity.MEDIUM,
                LoopCategory.STRING_CONCATENATION,
                "String concatenation in loop - inefficient for large datasets",
                "Use StringBuilder or StringBuffer for better performance",
                ['.java', '.cs'],
                2.0
            ),
            SuspiciousLoopPattern(
                r'(?:while|for)[^:]*:[^:]*?(?:\n(?:\s{4,}|\t+)[^\n]*)*?\w+\s*\+=\s*[\'"][^\'"]*[\'"]',
                LoopSeverity.MEDIUM,
                LoopCategory.STRING_CONCATENATION,
                "String concatenation in Python loop",
                "Use join() method or f-strings for better performance",
                ['.py'],
                2.0
            ),
            SuspiciousLoopPattern(
                r'(?:while|for)\s*\([^)]*\)\s*\{[^}]*(?:std::string.*\+=|strcat|sprintf)[^}]*\}',
                LoopSeverity.MEDIUM,
                LoopCategory.STRING_CONCATENATION,
                "String concatenation in C++ loop",
                "Consider using stringstream or reserve() for better performance",
                ['.cpp'],
                2.0
            ),
        ])
        
        # Collection Operations in Loops - More precise patterns
        patterns.extend([
            SuspiciousLoopPattern(
                r'(?:for|while)\s*\([^)]*\)\s*\{[^{}]{0,300}(?:\.contains\(|\.indexOf\(|\.remove\(|\.add\(.*\.get\()[^{}]{0,100}\}',
                LoopSeverity.MEDIUM,
                LoopCategory.COLLECTION_OPERATIONS,
                "Inefficient collection operations in loop",
                "Consider using Set for lookups or optimizing data structure access",
                ['.java', '.cs'],
                2.0
            ),
            SuspiciousLoopPattern(
                r'(?:for|while)\s+[^:]+:\s*\n(?:\s{4,}|\t+)[^\n]*(?:\.append\(|\.insert\(|\.remove\(|\s+in\s+)',
                LoopSeverity.MEDIUM,
                LoopCategory.COLLECTION_OPERATIONS,
                "Potentially inefficient collection operations in Python loop",
                "Consider using set for membership tests or list comprehensions",
                ['.py'],
                1.5
            ),
        ])
        
        # Recursive Calls in Loops - More precise patterns
        patterns.extend([
            SuspiciousLoopPattern(
                r'(?:for|while)\s*\([^)]*\)\s*\{[^{}]{0,200}(\w+)\s*\([^)]*\)[^{}]{0,100}\}',
                LoopSeverity.HIGH,
                LoopCategory.RECURSIVE_CALLS,
                "Recursive function calls inside loop",
                "Consider iterative approach or memoization",
                ['.java', '.js', '.ts', '.cpp', '.c', '.cs'],
                2.5
            ),
            SuspiciousLoopPattern(
                r'(?:for|while)\s+[^:]+:\s*\n(?:\s{4,}|\t+)[^\n]*(?:\w+)\s*\([^)]*\)',
                LoopSeverity.HIGH,
                LoopCategory.RECURSIVE_CALLS,
                "Recursive function calls inside Python loop",
                "Consider using iterative approach or functools.lru_cache",
                ['.py'],
                2.5
            ),
        ])
        
        # Database Operations in Loops
        patterns.extend([
            SuspiciousLoopPattern(
                r'(?:while|for)\s*\([^)]*\)\s*\{[^}]*(?:executeQuery|executeUpdate|PreparedStatement|Connection)[^}]*\}',
                LoopSeverity.HIGH,
                LoopCategory.BLOCKING_OPERATIONS,
                "Database operations inside loop",
                "Consider batch operations or connection pooling",
                ['.java', '.cs'],
                3.0
            ),
            SuspiciousLoopPattern(
                r'(?:while|for)[^:]*:[^:]*?(?:\n(?:\s{4,}|\t+)[^\n]*)*?(?:cursor\.execute|conn\.execute|\.commit\(\))',
                LoopSeverity.HIGH,
                LoopCategory.BLOCKING_OPERATIONS,
                "Database operations inside Python loop",
                "Consider using executemany() or batch operations",
                ['.py'],
                3.0
            ),
        ])
        
        # Large Data Processing in Loops
        patterns.extend([
            SuspiciousLoopPattern(
                r'(?:while|for)\s*\([^)]*\)\s*\{[^}]*(?:BufferedImage|Image|byte\[\].*new\s+byte|File.*listFiles)[^}]*\}',
                LoopSeverity.MEDIUM,
                LoopCategory.RESOURCE_INTENSIVE,
                "Resource-intensive operations in loop",
                "Consider parallel processing or memory management",
                ['.java'],
                2.0
            ),
            SuspiciousLoopPattern(
                r'(?:while|for)[^:]*:[^:]*?(?:\n(?:\s{4,}|\t+)[^\n]*)*?(?:cv2\.|PIL\.|numpy\.|pandas\.)',
                LoopSeverity.MEDIUM,
                LoopCategory.RESOURCE_INTENSIVE,
                "Heavy library operations in Python loop",
                "Consider vectorized operations or parallel processing",
                ['.py'],
                2.0
            ),
        ])
        
        # Sleep/Wait in Loops
        patterns.extend([
            SuspiciousLoopPattern(
                r'(?:while|for)\s*\([^)]*\)\s*\{[^}]*(?:Thread\.sleep|Thread\.wait|sleep\(|delay\()[^}]*\}',
                LoopSeverity.MEDIUM,
                LoopCategory.BLOCKING_OPERATIONS,
                "Sleep/wait operations in loop",
                "Consider using proper synchronization or event-driven approach",
                ['.java', '.js', '.ts', '.cs'],
                1.5
            ),
            SuspiciousLoopPattern(
                r'(?:while|for)[^:]*:[^:]*?(?:\n(?:\s{4,}|\t+)[^\n]*)*?(?:time\.sleep|asyncio\.sleep|threading\.)',
                LoopSeverity.MEDIUM,
                LoopCategory.BLOCKING_OPERATIONS,
                "Sleep/blocking operations in Python loop",
                "Consider using async/await or proper event handling",
                ['.py'],
                1.5
            ),
        ])
        
        return patterns
    
    def analyze_file(self, file_path: str) -> List[Dict]:
        """Analyze a single file for suspicious loops"""
        issues = []
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
            file_ext = os.path.splitext(file_path)[1].lower()
            
            for pattern in self.patterns:
                # Skip pattern if it doesn't apply to this file type
                if pattern.file_extensions and file_ext not in pattern.file_extensions:
                    continue
                    
                matches = pattern.pattern.finditer(content)
                for match in matches:
                    line_num = content[:match.start()].count('\n') + 1
                    
                    # Extract loop code context
                    loop_code_block = self._extract_loop_code_block(content, match)
                    
                    # Calculate complexity score
                    complexity_score = self._calculate_complexity_score(match.group(0), pattern)
                    
                    issue = {
                        'file': file_path,
                        'line': line_num,
                        'severity': pattern.severity.value,
                        'category': pattern.category.value,
                        'description': pattern.description,
                        'code': loop_code_block,
                        'recommendation': pattern.recommendation,
                        'match': match.group(0)[:200] + "..." if len(match.group(0)) > 200 else match.group(0),
                        'complexity_score': complexity_score
                    }
                    issues.append(issue)
                    self.stats[pattern.severity.value] += 1
                    self.stats[pattern.category.value] += 1
                    
        except Exception as e:
            logger.error(f"Error analyzing file {file_path}: {e}")
            
        return issues
    
    def _extract_loop_code_block(self, content: str, match: re.Match) -> str:
        """Extract the complete loop code block with context"""
        lines = content.split('\n')
        match_start_line = content[:match.start()].count('\n')
        match_end_line = content[:match.end()].count('\n')
        
        # Extract the matched portion to understand what we're dealing with
        matched_text = match.group(0)
        
        # If the match is too long (like entire file), just show the relevant lines
        if len(matched_text) > 500:
            # Find the actual loop line within the match
            match_lines = matched_text.split('\n')
            loop_line = None
            for i, line in enumerate(match_lines):
                if any(keyword in line for keyword in ['for', 'while', 'do']):
                    loop_line = line.strip()
                    break
            
            if loop_line:
                # Find this line in the original content
                for i, line in enumerate(lines):
                    if loop_line in line:
                        match_start_line = i
                        break
            
            # Show limited context around the actual loop
            context_start = max(0, match_start_line - 3)
            context_end = min(len(lines), match_start_line + 10)
        else:
            # Normal case - find loop boundaries
            start_line = match_start_line
            end_line = match_end_line
            
            # For languages with braces, find matching braces
            if '{' in matched_text:
                brace_count = 0
                for i in range(match_start_line, len(lines)):
                    line = lines[i]
                    brace_count += line.count('{') - line.count('}')
                    end_line = i
                    if brace_count <= 0 and i > match_start_line and '{' in lines[match_start_line]:
                        break
            
            # For Python-like indentation
            elif ':' in matched_text:
                base_indent = len(lines[match_start_line]) - len(lines[match_start_line].lstrip())
                for i in range(match_start_line + 1, len(lines)):
                    line = lines[i]
                    if line.strip() == '':
                        continue
                    current_indent = len(line) - len(line.lstrip())
                    if current_indent <= base_indent and line.strip():
                        end_line = i - 1
                        break
                    end_line = i
            
            # Extract context
            context_start = max(0, start_line - 2)
            context_end = min(len(lines), end_line + 3)
        
        # Build the code block with line numbers and markers
        code_block_lines = []
        for i in range(context_start, context_end):
            if i < len(lines):
                # Mark the suspicious loop lines
                if context_start <= i <= context_end and any(keyword in lines[i] for keyword in ['for', 'while', 'do']):
                    marker = ">>> "
                elif match_start_line <= i <= match_end_line:
                    marker = "    "
                else:
                    marker = "    "
                code_block_lines.append(f"{marker}{i+1:4}: {lines[i]}")
        
        # If still too long, truncate
        if len('\n'.join(code_block_lines)) > 800:
            truncated_lines = code_block_lines[:15]
            truncated_lines.append("    ... (truncated - loop body continues)")
            return '\n'.join(truncated_lines)
        
        return '\n'.join(code_block_lines)
    
    def _calculate_complexity_score(self, code_block: str, pattern: SuspiciousLoopPattern) -> float:
        """Calculate complexity score for a code block"""
        base_score = 1.0
        
        # Count nesting levels
        nesting_level = code_block.count('{') if '{' in code_block else code_block.count(':')
        base_score += nesting_level * 0.5
        
        # Count operations that increase complexity
        complexity_keywords = ['new ', 'malloc', 'calloc', 'append', 'insert', 'remove', 
                              'print', 'System.out', 'query', 'execute', 'sleep']
        for keyword in complexity_keywords:
            base_score += code_block.lower().count(keyword.lower()) * 0.2
        
        # Apply pattern-specific multiplier
        final_score = base_score * pattern.complexity_multiplier
        
        return round(final_score, 2)
    
    def analyze_jar_file(self, jar_path: str) -> List[Dict]:
        """Analyze Java classes within a JAR file"""
        issues = []
        
        try:
            with zipfile.ZipFile(jar_path, 'r') as jar:
                for file_info in jar.filelist:
                    if file_info.filename.endswith(('.java', '.js', '.py')):
                        try:
                            content = jar.read(file_info.filename).decode('utf-8', errors='ignore')
                            file_ext = os.path.splitext(file_info.filename)[1].lower()
                            
                            for pattern in self.patterns:
                                if pattern.file_extensions and file_ext not in pattern.file_extensions:
                                    continue
                                    
                                matches = pattern.pattern.finditer(content)
                                for match in matches:
                                    line_num = content[:match.start()].count('\n') + 1
                                    line_content = content.split('\n')[line_num - 1].strip()
                                    complexity_score = self._calculate_complexity_score(match.group(0), pattern)
                                    
                                    issue = {
                                        'file': f"{jar_path}:{file_info.filename}",
                                        'line': line_num,
                                        'severity': pattern.severity.value,
                                        'category': pattern.category.value,
                                        'description': pattern.description,
                                        'code': line_content,
                                        'recommendation': pattern.recommendation,
                                        'match': match.group(0)[:200] + "..." if len(match.group(0)) > 200 else match.group(0),
                                        'complexity_score': complexity_score
                                    }
                                    issues.append(issue)
                                    self.stats[pattern.severity.value] += 1
                                    self.stats[pattern.category.value] += 1
                        except Exception as e:
                            logger.debug(f"Could not analyze {file_info.filename} in {jar_path}: {e}")
                            
        except Exception as e:
            logger.error(f"Error analyzing JAR file {jar_path}: {e}")
            
        return issues
    
    def scan_directory(self, directory: str, extensions: List[str] = None) -> Dict:
        """Scan directory for suspicious loop patterns"""
        if extensions is None:
            extensions = ['.java', '.py', '.js', '.ts', '.cpp', '.c', '.cs', '.jar']
        
        all_issues = []
        file_patterns = []
        
        # Add file patterns for each extension
        for ext in extensions:
            file_patterns.append(f"**/*{ext}")
        
        logger.info(f"Scanning directory: {directory}")
        logger.info(f"Looking for files with extensions: {extensions}")
        
        for pattern in file_patterns:
            search_pattern = os.path.join(directory, pattern)
            files = glob.glob(search_pattern, recursive=True)
            
            for file_path in files:
                if os.path.isfile(file_path):
                    self.scanned_files += 1
                    logger.debug(f"Analyzing file: {file_path}")
                    
                    if file_path.endswith('.jar'):
                        issues = self.analyze_jar_file(file_path)
                    else:
                        issues = self.analyze_file(file_path)
                    
                    all_issues.extend(issues)
                    self.results[file_path].extend(issues)
        
        self.total_issues = len(all_issues)
        
        return {
            'issues': all_issues,
            'stats': dict(self.stats),
            'scanned_files': self.scanned_files,
            'total_issues': self.total_issues
        }
    
    def generate_report(self, results: Dict, output_file: str = None) -> str:
        """Generate detailed suspicious loops report"""
        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            reports_dir = os.path.join(os.path.dirname(__file__), 'suspicious_loops_reports')
            os.makedirs(reports_dir, exist_ok=True)
            output_file = os.path.join(reports_dir, f'suspicious_loops_report_{timestamp}.txt')
        
        report_lines = []
        report_lines.append("=" * 80)
        report_lines.append("SUSPICIOUS LOOPS DETECTION REPORT")
        report_lines.append("=" * 80)
        report_lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append(f"Scanned Files: {results['scanned_files']}")
        report_lines.append(f"Total Issues Found: {results['total_issues']}")
        report_lines.append("")
        
        # Summary by severity
        report_lines.append("ISSUES BY SEVERITY:")
        report_lines.append("-" * 30)
        for severity in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO']:
            count = results['stats'].get(severity, 0)
            report_lines.append(f"{severity:12}: {count}")
        report_lines.append("")
        
        # Summary by category
        report_lines.append("ISSUES BY CATEGORY:")
        report_lines.append("-" * 30)
        for category in LoopCategory:
            count = results['stats'].get(category.value, 0)
            if count > 0:
                report_lines.append(f"{category.value:25}: {count}")
        report_lines.append("")
        
        # Top complexity issues
        if results['issues']:
            sorted_issues = sorted(results['issues'], key=lambda x: x.get('complexity_score', 0), reverse=True)
            report_lines.append("TOP COMPLEXITY ISSUES:")
            report_lines.append("-" * 30)
            for i, issue in enumerate(sorted_issues[:5], 1):
                report_lines.append(f"{i}. Complexity: {issue.get('complexity_score', 0)} - {issue['category']}")
                report_lines.append(f"   File: {issue['file']}:{issue['line']}")
                report_lines.append(f"   Issue: {issue['description']}")
                report_lines.append("")
        
        # Detailed issues
        if results['issues']:
            report_lines.append("DETAILED ISSUES:")
            report_lines.append("=" * 50)
            
            # Group by severity
            issues_by_severity = defaultdict(list)
            for issue in results['issues']:
                issues_by_severity[issue['severity']].append(issue)
            
            for severity in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO']:
                issues = issues_by_severity.get(severity, [])
                if issues:
                    report_lines.append(f"\n{severity} SEVERITY ISSUES ({len(issues)}):")
                    report_lines.append("-" * 40)
                    
                    # Sort by complexity score within severity
                    issues.sort(key=lambda x: x.get('complexity_score', 0), reverse=True)
                    
                    for i, issue in enumerate(issues, 1):
                        report_lines.append(f"\n{i}. {issue['category']} (Complexity: {issue.get('complexity_score', 0)})")
                        report_lines.append(f"   File: {issue['file']}")
                        report_lines.append(f"   Line: {issue['line']}")
                        report_lines.append(f"   Issue: {issue['description']}")
                        report_lines.append(f"   Loop Code Block:")
                        # Add the code block with proper indentation
                        code_lines = issue['code'].split('\n')
                        for code_line in code_lines:
                            report_lines.append(f"     {code_line}")
                        if issue['recommendation']:
                            report_lines.append(f"   Fix: {issue['recommendation']}")
        else:
            report_lines.append("No suspicious loop patterns detected!")
        
        # Performance Optimization Recommendations
        report_lines.append("\n" + "=" * 80)
        report_lines.append("LOOP PERFORMANCE OPTIMIZATION RECOMMENDATIONS")
        report_lines.append("=" * 80)
        
        recommendations = [
            "1. Avoid infinite loops - always have proper exit conditions",
            "2. Move object allocation outside loops when possible",
            "3. Use efficient data structures for lookups (Set instead of List)",
            "4. Minimize I/O operations inside loops - consider batching",
            "5. Avoid string concatenation in loops - use StringBuilder",
            "6. Consider algorithm complexity - avoid nested loops when possible",
            "7. Use parallel processing for independent iterations",
            "8. Profile your code to identify actual bottlenecks",
            "9. Consider using built-in functions and libraries",
            "10. Cache expensive computations outside loops",
            "11. Use iterators and generators for memory efficiency",
            "12. Implement proper error handling to prevent infinite loops"
        ]
        
        report_lines.extend(recommendations)
        report_lines.append("")
        
        report_content = '\n'.join(report_lines)
        
        # Write to file
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(report_content)
            logger.info(f"Report saved to: {output_file}")
        except Exception as e:
            logger.error(f"Error saving report: {e}")
        
        return report_content

def main():
    """Main function for command-line usage"""
    try:
        parser = argparse.ArgumentParser(description='Suspicious Loops Detector')
        parser.add_argument('directory', help='Directory to scan')
        parser.add_argument('--extensions', nargs='+', 
                           default=['.java', '.py', '.js', '.ts', '.cpp', '.c', '.cs', '.jar'],
                           help='File extensions to scan')
        parser.add_argument('--output', help='Output report file')
        parser.add_argument('--json-output', help='JSON output file')
        parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
        
        args = parser.parse_args()
        
        if args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        
        detector = SuspiciousLoopsDetector()
        results = detector.scan_directory(args.directory, args.extensions)
        
        # Generate text report
        report = detector.generate_report(results, args.output)
        print(report)
        
        # Generate JSON report if requested
        if args.json_output:
            try:
                with open(args.json_output, 'w', encoding='utf-8') as f:
                    json.dump(results, f, indent=2, ensure_ascii=False)
                logger.info(f"JSON report saved to: {args.json_output}")
            except Exception as e:
                logger.error(f"Error saving JSON report: {e}")
                
    except Exception as e:
        logger.error(f"Error in main execution: {e}")
        print(f"Error: {e}")
    
    finally:
        # Prevent window from closing immediately
        if len(sys.argv) <= 1:  # Interactive mode
            input("Press Enter to continue...")

if __name__ == '__main__':
    if len(sys.argv) > 1:
        main()
    else:
        # Interactive mode
        print("Suspicious Loops Detector")
        print("========================")
        
        try:
            directory = input("Enter directory to scan: ").strip()
            if not directory:
                directory = "."
            
            if not os.path.exists(directory):
                print(f"Directory '{directory}' does not exist!")
                input("Press Enter to continue...")
                sys.exit(1)
            
            detector = SuspiciousLoopsDetector()
            results = detector.scan_directory(directory)
            report = detector.generate_report(results)
            print(report)
            
        except Exception as e:
            print(f"Error: {e}")
            logger.error(f"Error in interactive mode: {e}")
        
        finally:
            input("Press Enter to continue...")