#!/usr/bin/env python3
"""
Memory Leak Detector for Code Analysis
======================================

Detects potential memory leaks in source code including:
- Unclosed resources (files, streams, connections)
- Infinite loops with memory allocation
- Circular references
- Collections that grow indefinitely
- Missing garbage collection hints
- Resource management anti-patterns

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
import struct
import traceback
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

class MemoryLeakSeverity(Enum):
    """Memory leak severity levels"""
    CRITICAL = "CRITICAL"  # Definite memory leaks
    HIGH = "HIGH"         # Very likely memory leaks
    MEDIUM = "MEDIUM"     # Potential memory leaks
    LOW = "LOW"           # Memory usage patterns to watch
    INFO = "INFO"         # General memory optimization suggestions

class MemoryLeakCategory(Enum):
    """Categories of memory leak issues"""
    UNCLOSED_RESOURCES = "Unclosed Resources"
    INFINITE_ALLOCATION = "Infinite Allocation"
    CIRCULAR_REFERENCES = "Circular References"
    GROWING_COLLECTIONS = "Growing Collections"
    RESOURCE_MANAGEMENT = "Resource Management"
    GARBAGE_COLLECTION = "Garbage Collection"
    CACHING_ISSUES = "Caching Issues"
    THREAD_LEAKS = "Thread Leaks"

class MemoryLeakPattern:
    """Represents a memory leak pattern"""
    def __init__(self, pattern: str, severity: MemoryLeakSeverity, 
                 category: MemoryLeakCategory, description: str, 
                 recommendation: str = "", file_extensions: List[str] = None):
        self.pattern = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        self.severity = severity
        self.category = category
        self.description = description
        self.recommendation = recommendation
        self.file_extensions = file_extensions or []

class MemoryLeakDetector:
    """Main memory leak detector class"""
    
    def __init__(self):
        self.patterns = self._initialize_patterns()
        self.results = defaultdict(list)
        self.stats = defaultdict(int)
        self.scanned_files = 0
        self.total_issues = 0
        
    def _initialize_patterns(self) -> List[MemoryLeakPattern]:
        """Initialize memory leak detection patterns"""
        patterns = []
        
        # Unclosed Resources - Java
        patterns.extend([
            MemoryLeakPattern(
                r'new\s+FileInputStream\s*\([^)]+\)[^;]*(?!.*\.close\(\))',
                MemoryLeakSeverity.HIGH,
                MemoryLeakCategory.UNCLOSED_RESOURCES,
                "FileInputStream created without explicit close()",
                "Use try-with-resources or ensure close() is called in finally block",
                ['.java']
            ),
            MemoryLeakPattern(
                r'new\s+FileOutputStream\s*\([^)]+\)[^;]*(?!.*\.close\(\))',
                MemoryLeakSeverity.HIGH,
                MemoryLeakCategory.UNCLOSED_RESOURCES,
                "FileOutputStream created without explicit close()",
                "Use try-with-resources or ensure close() is called in finally block",
                ['.java']
            ),
            MemoryLeakPattern(
                r'new\s+BufferedReader\s*\([^)]+\)[^;]*(?!.*\.close\(\))',
                MemoryLeakSeverity.HIGH,
                MemoryLeakCategory.UNCLOSED_RESOURCES,
                "BufferedReader created without explicit close()",
                "Use try-with-resources or ensure close() is called",
                ['.java']
            ),
            MemoryLeakPattern(
                r'new\s+Scanner\s*\([^)]+\)[^;]*(?!.*\.close\(\))',
                MemoryLeakSeverity.MEDIUM,
                MemoryLeakCategory.UNCLOSED_RESOURCES,
                "Scanner created without explicit close()",
                "Call close() on Scanner to free resources",
                ['.java']
            ),
            MemoryLeakPattern(
                r'DriverManager\.getConnection\s*\([^)]+\)[^;]*(?!.*\.close\(\))',
                MemoryLeakSeverity.CRITICAL,
                MemoryLeakCategory.UNCLOSED_RESOURCES,
                "Database connection created without explicit close()",
                "Always close database connections in finally block or use try-with-resources",
                ['.java']
            ),
        ])
        
        # Unclosed Resources - Python
        patterns.extend([
            MemoryLeakPattern(
                r'open\s*\([^)]+\)[^;]*(?!.*\.close\(\))(?!.*with\s)',
                MemoryLeakSeverity.HIGH,
                MemoryLeakCategory.UNCLOSED_RESOURCES,
                "File opened without explicit close() or context manager",
                "Use 'with open()' context manager or call close() explicitly",
                ['.py']
            ),
            MemoryLeakPattern(
                r'urllib\.request\.urlopen\s*\([^)]+\)[^;]*(?!.*\.close\(\))',
                MemoryLeakSeverity.MEDIUM,
                MemoryLeakCategory.UNCLOSED_RESOURCES,
                "URL connection opened without explicit close()",
                "Use context manager or call close() on the response",
                ['.py']
            ),
        ])
        
        # Infinite Loops with Memory Allocation
        patterns.extend([
            MemoryLeakPattern(
                r'while\s*\(\s*true\s*\).*(?:new\s+\w+|ArrayList|HashMap|Vector)',
                MemoryLeakSeverity.CRITICAL,
                MemoryLeakCategory.INFINITE_ALLOCATION,
                "Infinite loop with object allocation detected",
                "Ensure loop has proper exit condition and cleanup",
                ['.java', '.js', '.cpp', '.c']
            ),
            MemoryLeakPattern(
                r'for\s*\(\s*;\s*;\s*\).*(?:new\s+\w+|ArrayList|HashMap)',
                MemoryLeakSeverity.CRITICAL,
                MemoryLeakCategory.INFINITE_ALLOCATION,
                "Infinite for loop with object allocation",
                "Add proper loop termination condition",
                ['.java', '.js', '.cpp', '.c']
            ),
            MemoryLeakPattern(
                r'while\s+True:.*(?:list\(\)|dict\(\)|\[\]|\{\}|append|extend)',
                MemoryLeakSeverity.CRITICAL,
                MemoryLeakCategory.INFINITE_ALLOCATION,
                "Infinite Python loop with memory allocation",
                "Add break condition and ensure proper cleanup",
                ['.py']
            ),
        ])
        
        # Growing Collections without bounds
        patterns.extend([
            MemoryLeakPattern(
                r'(?:ArrayList|HashMap|Vector|LinkedList)\s+\w+.*\.add\([^)]+\)(?!.*\.clear\(\)|.*\.remove\()',
                MemoryLeakSeverity.MEDIUM,
                MemoryLeakCategory.GROWING_COLLECTIONS,
                "Collection grows without clear/remove operations",
                "Implement cleanup mechanism or size limits",
                ['.java']
            ),
            MemoryLeakPattern(
                r'(?:list|dict|set)\s*\([^)]*\).*\.(?:append|add|update)(?!.*\.clear\(\)|.*\.pop\(\)|.*del\s)',
                MemoryLeakSeverity.MEDIUM,
                MemoryLeakCategory.GROWING_COLLECTIONS,
                "Python collection grows without cleanup",
                "Implement periodic cleanup or size limits",
                ['.py']
            ),
        ])
        
        # Static Collections (potential memory leaks)
        patterns.extend([
            MemoryLeakPattern(
                r'static\s+(?:final\s+)?(?:List|Map|Set|Collection)\s*<[^>]*>\s+\w+\s*=\s*new',
                MemoryLeakSeverity.MEDIUM,
                MemoryLeakCategory.GROWING_COLLECTIONS,
                "Static collection that may grow indefinitely",
                "Consider using WeakReference or implement cleanup",
                ['.java']
            ),
        ])
        
        # Circular References
        patterns.extend([
            MemoryLeakPattern(
                r'class\s+\w+.*\{[^}]*\w+\s+parent[^}]*\w+\s+child',
                MemoryLeakSeverity.MEDIUM,
                MemoryLeakCategory.CIRCULAR_REFERENCES,
                "Potential circular reference between parent and child",
                "Use WeakReference for parent references",
                ['.java']
            ),
            MemoryLeakPattern(
                r'self\.\w+\s*=.*self',
                MemoryLeakSeverity.LOW,
                MemoryLeakCategory.CIRCULAR_REFERENCES,
                "Potential self-reference in Python",
                "Be careful with circular references, use weak references if needed",
                ['.py']
            ),
        ])
        
        # Caching Issues
        patterns.extend([
            MemoryLeakPattern(
                r'(?:Cache|Map|HashMap).*put\([^)]+\)(?!.*(?:evict|remove|clear|size))',
                MemoryLeakSeverity.MEDIUM,
                MemoryLeakCategory.CACHING_ISSUES,
                "Cache without eviction policy",
                "Implement cache size limits and eviction policy",
                ['.java']
            ),
            MemoryLeakPattern(
                r'@lru_cache\s*(?:\(.*maxsize\s*=\s*None|\(\))',
                MemoryLeakSeverity.MEDIUM,
                MemoryLeakCategory.CACHING_ISSUES,
                "LRU cache without size limit",
                "Set maxsize parameter to prevent unlimited growth",
                ['.py']
            ),
        ])
        
        # Thread Leaks
        patterns.extend([
            MemoryLeakPattern(
                r'new\s+Thread\s*\([^)]+\)\.start\(\)(?!.*\.join\(\))',
                MemoryLeakSeverity.HIGH,
                MemoryLeakCategory.THREAD_LEAKS,
                "Thread started without join() or proper lifecycle management",
                "Ensure threads are properly terminated with join() or interrupt()",
                ['.java']
            ),
            MemoryLeakPattern(
                r'threading\.Thread\([^)]+\)\.start\(\)(?!.*\.join\(\))',
                MemoryLeakSeverity.HIGH,
                MemoryLeakCategory.THREAD_LEAKS,
                "Python thread started without join()",
                "Call join() on threads or use proper thread pool management",
                ['.py']
            ),
        ])
        
        # Resource Management Anti-patterns
        patterns.extend([
            MemoryLeakPattern(
                r'finalize\s*\(\s*\)\s*\{[^}]*(?!super\.finalize)',
                MemoryLeakSeverity.MEDIUM,
                MemoryLeakCategory.RESOURCE_MANAGEMENT,
                "Finalize method without calling super.finalize()",
                "Always call super.finalize() in finalize methods",
                ['.java']
            ),
            MemoryLeakPattern(
                r'System\.gc\s*\(\s*\)',
                MemoryLeakSeverity.LOW,
                MemoryLeakCategory.GARBAGE_COLLECTION,
                "Explicit garbage collection call",
                "Avoid explicit GC calls, let JVM handle garbage collection",
                ['.java']
            ),
        ])
        
        # JavaScript specific patterns
        patterns.extend([
            MemoryLeakPattern(
                r'setInterval\s*\([^)]+\)(?!.*clearInterval)',
                MemoryLeakSeverity.HIGH,
                MemoryLeakCategory.UNCLOSED_RESOURCES,
                "setInterval without clearInterval",
                "Store interval ID and call clearInterval when done",
                ['.js', '.ts']
            ),
            MemoryLeakPattern(
                r'setTimeout\s*\([^)]+\)(?!.*clearTimeout)',
                MemoryLeakSeverity.MEDIUM,
                MemoryLeakCategory.UNCLOSED_RESOURCES,
                "setTimeout without clearTimeout (potential if recurring)",
                "Consider clearTimeout if timeout needs to be cancelled",
                ['.js', '.ts']
            ),
            MemoryLeakPattern(
                r'addEventListener\s*\([^)]+\)(?!.*removeEventListener)',
                MemoryLeakSeverity.MEDIUM,
                MemoryLeakCategory.UNCLOSED_RESOURCES,
                "Event listener added without removal",
                "Call removeEventListener when element is no longer needed",
                ['.js', '.ts']
            ),
        ])
        
        return patterns
    
    def analyze_file(self, file_path: str) -> List[Dict]:
        """Analyze a single file for memory leaks"""
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
                    
                    # Extract more context around the match
                    code_block, leak_analysis = self._extract_code_context(content, match, pattern)
                    
                    issue = {
                        'file': file_path,
                        'line': line_num,
                        'severity': pattern.severity.value,
                        'category': pattern.category.value,
                        'description': pattern.description,
                        'code': code_block,
                        'recommendation': pattern.recommendation,
                        'match': match.group(0)[:200] + "..." if len(match.group(0)) > 200 else match.group(0),
                        'leak_analysis': leak_analysis
                    }
                    issues.append(issue)
                    self.stats[pattern.severity.value] += 1
                    self.stats[pattern.category.value] += 1
                    
        except Exception as e:
            logger.error(f"Error analyzing file {file_path}: {e}")
            
        return issues
    
    def _extract_code_context(self, content: str, match: re.Match, pattern: MemoryLeakPattern) -> Tuple[str, str]:
        """Extract code context and analyze for memory leaks"""
        lines = content.split('\n')
        match_start_line = content[:match.start()].count('\n')
        
        # Extract more context (5 lines before and after)
        start_line = max(0, match_start_line - 2)
        end_line = min(len(lines), match_start_line + 8)
        
        code_block = '\n'.join([f"{i+1:4}: {lines[i]}" for i in range(start_line, end_line)])
        
        # Analyze if this is actually a memory leak
        leak_analysis = self._analyze_memory_leak(content, match, pattern)
        
        return code_block, leak_analysis
    
    def _analyze_memory_leak(self, content: str, match: re.Match, pattern: MemoryLeakPattern) -> str:
        """Analyze if the matched pattern actually causes a memory leak"""
        matched_text = match.group(0)
        
        # Get surrounding context (200 chars before and after)
        start_pos = max(0, match.start() - 200)
        end_pos = min(len(content), match.end() + 200)
        context = content[start_pos:end_pos]
        
        if pattern.category == MemoryLeakCategory.UNCLOSED_RESOURCES:
            # Check for proper resource management
            if 'try-with-resources' in context or 'try (' in context:
                return "✓ Resource is properly managed with try-with-resources - NO MEMORY LEAK detected"
            elif '.close()' in context:
                return "✓ Resource has explicit close() call - NO MEMORY LEAK detected (verify close() is in finally block)"
            elif 'finally' in context and '.close()' in context:
                return "✓ Resource is closed in finally block - NO MEMORY LEAK detected"
            elif 'with open(' in context:
                return "✓ Resource uses Python context manager - NO MEMORY LEAK detected"
            else:
                return "⚠ MEMORY LEAK DETECTED: Resource allocation without proper cleanup - resource will not be closed"
                
        elif pattern.category == MemoryLeakCategory.INFINITE_ALLOCATION:
            if 'break' in context or 'return' in context or 'throw' in context:
                return "✓ Loop has exit condition - NO MEMORY LEAK detected"
            else:
                return "⚠ MEMORY LEAK DETECTED: Infinite loop with memory allocation - will cause OutOfMemoryError"
                
        elif pattern.category == MemoryLeakCategory.GROWING_COLLECTIONS:
            if '.clear()' in context or '.remove(' in context or 'size()' in context:
                return "✓ Collection has cleanup mechanisms - NO MEMORY LEAK detected"
            elif 'WeakReference' in context or 'WeakHashMap' in context:
                return "✓ Uses weak references - NO MEMORY LEAK detected"
            else:
                return "⚠ POTENTIAL MEMORY LEAK: Collection grows without cleanup - may cause memory exhaustion"
                
        elif pattern.category == MemoryLeakCategory.THREAD_LEAKS:
            if '.join()' in context or '.interrupt()' in context:
                return "✓ Thread has proper lifecycle management - NO MEMORY LEAK detected"
            else:
                return "⚠ MEMORY LEAK DETECTED: Thread started without proper cleanup - threads will accumulate"
                
        elif pattern.category == MemoryLeakCategory.CACHING_ISSUES:
            if 'maxsize' in context or 'evict' in context or 'LRU' in context:
                return "✓ Cache has size limits or eviction policy - NO MEMORY LEAK detected"
            else:
                return "⚠ POTENTIAL MEMORY LEAK: Cache without size limits - may grow indefinitely"
                
        elif pattern.category == MemoryLeakCategory.CIRCULAR_REFERENCES:
            if 'WeakReference' in context or 'weak' in context.lower():
                return "✓ Uses weak references to avoid circular references - NO MEMORY LEAK detected"
            else:
                return "⚠ POTENTIAL MEMORY LEAK: Circular reference detected - may prevent garbage collection"
                
        else:
            return "⚠ POTENTIAL MEMORY LEAK: Pattern detected that may cause memory issues"
    
    def analyze_class_file(self, class_path: str) -> List[Dict]:
        """Analyze Java .class file for memory leak patterns"""
        issues = []
        
        try:
            with open(class_path, 'rb') as f:
                class_data = f.read()
            
            # Basic bytecode analysis - look for patterns in the bytecode
            # This is a simplified approach since full bytecode analysis would require
            # a complete Java class file parser
            
            # Convert bytecode to string representation for pattern matching
            try:
                # Try to extract string literals from the constant pool
                strings = self._extract_strings_from_class(class_data)
                
                # Analyze extracted strings for memory leak patterns
                for string_literal in strings:
                    # Check for JDBC patterns
                    if any(pattern in string_literal.lower() for pattern in 
                           ['getconnection', 'drivermanager', 'jdbc:']):
                        issues.append({
                            'file': class_path,
                            'line': 0,  # Line numbers not available in bytecode
                            'severity': MemoryLeakSeverity.MEDIUM.value,
                            'category': MemoryLeakCategory.UNCLOSED_RESOURCES.value,
                            'description': 'Database connection usage detected in bytecode',
                            'code': f'String literal: {string_literal[:100]}...' if len(string_literal) > 100 else string_literal,
                            'recommendation': 'Ensure database connections are properly closed',
                            'match': string_literal
                        })
                    
                    # Check for file I/O patterns
                    if any(pattern in string_literal.lower() for pattern in 
                           ['fileinputstream', 'fileoutputstream', 'bufferedreader']):
                        issues.append({
                            'file': class_path,
                            'line': 0,
                            'severity': MemoryLeakSeverity.MEDIUM.value,
                            'category': MemoryLeakCategory.UNCLOSED_RESOURCES.value,
                            'description': 'File I/O operation detected in bytecode',
                            'code': f'String literal: {string_literal[:100]}...' if len(string_literal) > 100 else string_literal,
                            'recommendation': 'Ensure file streams are properly closed',
                            'match': string_literal
                        })
                
                # Analyze bytecode instruction patterns
                bytecode_str = ' '.join(f'{b:02x}' for b in class_data)
                
                # Look for patterns that might indicate infinite loops
                # Java bytecode: goto instructions (0xa7) followed by negative offsets
                if re.search(r'a7[0-9a-f]{4}', bytecode_str):
                    issues.append({
                        'file': class_path,
                        'line': 0,
                        'severity': MemoryLeakSeverity.LOW.value,
                        'category': MemoryLeakCategory.INFINITE_ALLOCATION.value,
                        'description': 'Loop structure detected in bytecode (potential infinite loop)',
                        'code': 'Bytecode contains goto instructions',
                        'recommendation': 'Review loop conditions for proper termination',
                        'match': 'goto bytecode pattern'
                    })
                
                # Look for new object allocation patterns
                # Java bytecode: new instruction (0xbb)
                new_count = bytecode_str.count('bb')
                if new_count > 50:  # Arbitrary threshold for excessive object creation
                    issues.append({
                        'file': class_path,
                        'line': 0,
                        'severity': MemoryLeakSeverity.MEDIUM.value,
                        'category': MemoryLeakCategory.GROWING_COLLECTIONS.value,
                        'description': f'High number of object allocations detected ({new_count} new instructions)',
                        'code': f'{new_count} object allocation instructions found',
                        'recommendation': 'Review object creation patterns and implement object pooling if needed',
                        'match': f'{new_count} new instructions'
                    })
                    
            except Exception as e:
                logger.debug(f"Error analyzing bytecode patterns in {class_path}: {e}")
                # Even if detailed analysis fails, we can still report that we found a class file
                issues.append({
                    'file': class_path,
                    'line': 0,
                    'severity': MemoryLeakSeverity.INFO.value,
                    'category': MemoryLeakCategory.RESOURCE_MANAGEMENT.value,
                    'description': 'Java class file detected - manual review recommended',
                    'code': 'Compiled Java bytecode',
                    'recommendation': 'Review source code for memory management patterns',
                    'match': 'Java class file'
                })
            
            # Update stats
            for issue in issues:
                self.stats[issue['severity']] += 1
                self.stats[issue['category']] += 1
                
        except Exception as e:
            logger.error(f"Error analyzing class file {class_path}: {e}")
            
        return issues
    
    def _extract_strings_from_class(self, class_data: bytes) -> List[str]:
        """Extract string literals from Java class file constant pool"""
        strings = []
        
        try:
            # Java class file format: skip magic number (4 bytes) and version info (4 bytes)
            if len(class_data) < 8:
                return strings
                
            offset = 8
            
            # Read constant pool count
            if len(class_data) < offset + 2:
                return strings
                
            constant_pool_count = struct.unpack('>H', class_data[offset:offset+2])[0]
            offset += 2
            
            # Parse constant pool entries
            for i in range(1, constant_pool_count):
                if offset >= len(class_data):
                    break
                    
                tag = class_data[offset]
                offset += 1
                
                if tag == 1:  # CONSTANT_Utf8
                    if offset + 2 > len(class_data):
                        break
                    length = struct.unpack('>H', class_data[offset:offset+2])[0]
                    offset += 2
                    
                    if offset + length > len(class_data):
                        break
                        
                    try:
                        string_val = class_data[offset:offset+length].decode('utf-8')
                        strings.append(string_val)
                    except UnicodeDecodeError:
                        pass  # Skip invalid UTF-8 strings
                    
                    offset += length
                elif tag in [3, 4]:  # CONSTANT_Integer, CONSTANT_Float
                    offset += 4
                elif tag in [5, 6]:  # CONSTANT_Long, CONSTANT_Double
                    offset += 8
                    i += 1  # These take up two constant pool entries
                elif tag in [7, 8]:  # CONSTANT_Class, CONSTANT_String
                    offset += 2
                elif tag in [9, 10, 11, 12]:  # Various reference types
                    offset += 4
                elif tag == 15:  # CONSTANT_MethodHandle
                    offset += 3
                elif tag == 16:  # CONSTANT_MethodType
                    offset += 2
                elif tag == 18:  # CONSTANT_InvokeDynamic
                    offset += 4
                else:
                    # Unknown tag, try to continue but might be unreliable
                    break
                    
        except (struct.error, IndexError) as e:
            logger.debug(f"Error parsing class file constant pool: {e}")
            
        return strings
    
    def analyze_jar_file(self, jar_path: str) -> List[Dict]:
        """Analyze Java classes and other files within a JAR file"""
        issues = []
        
        try:
            with zipfile.ZipFile(jar_path, 'r') as jar:
                for file_info in jar.filelist:
                    if file_info.filename.endswith('.class'):
                        # Analyze .class files for bytecode patterns
                        try:
                            class_data = jar.read(file_info.filename)
                            
                            # Extract strings from class file
                            strings = self._extract_strings_from_class(class_data)
                            
                            # Analyze extracted strings for memory leak patterns
                            for string_literal in strings:
                                # Check for JDBC patterns
                                if any(pattern in string_literal.lower() for pattern in 
                                       ['getconnection', 'drivermanager', 'jdbc:']):
                                    issues.append({
                                        'file': f"{jar_path}:{file_info.filename}",
                                        'line': 0,
                                        'severity': MemoryLeakSeverity.MEDIUM.value,
                                        'category': MemoryLeakCategory.UNCLOSED_RESOURCES.value,
                                        'description': 'Database connection usage detected in class file',
                                        'code': f'String: {string_literal[:50]}...' if len(string_literal) > 50 else string_literal,
                                        'recommendation': 'Ensure database connections are properly closed',
                                        'match': string_literal
                                    })
                                
                                # Check for file I/O patterns
                                if any(pattern in string_literal.lower() for pattern in 
                                       ['fileinputstream', 'fileoutputstream', 'bufferedreader', 'scanner']):
                                    issues.append({
                                        'file': f"{jar_path}:{file_info.filename}",
                                        'line': 0,
                                        'severity': MemoryLeakSeverity.MEDIUM.value,
                                        'category': MemoryLeakCategory.UNCLOSED_RESOURCES.value,
                                        'description': 'File I/O operation detected in class file',
                                        'code': f'String: {string_literal[:50]}...' if len(string_literal) > 50 else string_literal,
                                        'recommendation': 'Ensure file streams are properly closed',
                                        'match': string_literal
                                    })
                                
                                # Check for thread-related patterns
                                if any(pattern in string_literal.lower() for pattern in 
                                       ['thread', 'runnable', 'executor']):
                                    issues.append({
                                        'file': f"{jar_path}:{file_info.filename}",
                                        'line': 0,
                                        'severity': MemoryLeakSeverity.LOW.value,
                                        'category': MemoryLeakCategory.THREAD_LEAKS.value,
                                        'description': 'Thread usage detected in class file',
                                        'code': f'String: {string_literal[:50]}...' if len(string_literal) > 50 else string_literal,
                                        'recommendation': 'Ensure proper thread lifecycle management',
                                        'match': string_literal
                                    })
                            
                            # Analyze bytecode patterns
                            bytecode_str = ' '.join(f'{b:02x}' for b in class_data)
                            
                            # Count object allocations
                            new_count = bytecode_str.count('bb')  # 'new' instruction
                            if new_count > 20:  # Threshold for class files
                                issues.append({
                                    'file': f"{jar_path}:{file_info.filename}",
                                    'line': 0,
                                    'severity': MemoryLeakSeverity.LOW.value,
                                    'category': MemoryLeakCategory.GROWING_COLLECTIONS.value,
                                    'description': f'High object allocation count in class ({new_count} allocations)',
                                    'code': f'{new_count} object allocation instructions',
                                    'recommendation': 'Review object creation patterns',
                                    'match': f'{new_count} new instructions'
                                })
                                
                        except Exception as e:
                            logger.debug(f"Could not analyze class file {file_info.filename} in {jar_path}: {e}")
                            
                    elif file_info.filename.endswith(('.java', '.js', '.py')):
                        # Analyze source files
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
                                    
                                    issue = {
                                        'file': f"{jar_path}:{file_info.filename}",
                                        'line': line_num,
                                        'severity': pattern.severity.value,
                                        'category': pattern.category.value,
                                        'description': pattern.description,
                                        'code': line_content,
                                        'recommendation': pattern.recommendation,
                                        'match': match.group(0)
                                    }
                                    issues.append(issue)
                                    self.stats[pattern.severity.value] += 1
                                    self.stats[pattern.category.value] += 1
                        except Exception as e:
                            logger.debug(f"Could not analyze {file_info.filename} in {jar_path}: {e}")
            
            # Update stats for class file issues
            for issue in issues:
                if issue['file'].endswith('.class'):
                    self.stats[issue['severity']] += 1
                    self.stats[issue['category']] += 1
                            
        except Exception as e:
            logger.error(f"Error analyzing JAR file {jar_path}: {e}")
            
        return issues
    
    def scan_directory(self, directory: str, extensions: List[str] = None) -> Dict:
        """Scan directory for memory leak patterns"""
        if extensions is None:
            extensions = ['.java', '.py', '.js', '.ts', '.cpp', '.c', '.jar', '.class']
        
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
                    
                    try:
                        if file_path.endswith('.jar'):
                            issues = self.analyze_jar_file(file_path)
                        elif file_path.endswith('.class'):
                            issues = self.analyze_class_file(file_path)
                        else:
                            issues = self.analyze_file(file_path)
                        
                        all_issues.extend(issues)
                        self.results[file_path].extend(issues)
                    except Exception as e:
                        logger.error(f"Error processing file {file_path}: {e}")
                        logger.debug(f"Stack trace: {traceback.format_exc()}")
        
        self.total_issues = len(all_issues)
        
        return {
            'issues': all_issues,
            'stats': dict(self.stats),
            'scanned_files': self.scanned_files,
            'total_issues': self.total_issues
        }
    
    def generate_report(self, results: Dict, output_file: str = None) -> str:
        """Generate detailed memory leak report"""
        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            reports_dir = os.path.join(os.path.dirname(__file__), 'memory_leak_reports')
            os.makedirs(reports_dir, exist_ok=True)
            output_file = os.path.join(reports_dir, f'memory_leak_report_{timestamp}.txt')
        
        report_lines = []
        report_lines.append("=" * 80)
        report_lines.append("MEMORY LEAK DETECTION REPORT")
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
        for category in MemoryLeakCategory:
            count = results['stats'].get(category.value, 0)
            if count > 0:
                report_lines.append(f"{category.value:25}: {count}")
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
                    
                    for i, issue in enumerate(issues, 1):
                        report_lines.append(f"\n{i}. {issue['category']}")
                        report_lines.append(f"   File: {issue['file']}")
                        report_lines.append(f"   Line: {issue['line']}")
                        report_lines.append(f"   Issue: {issue['description']}")
                        report_lines.append(f"   Analysis: {issue.get('leak_analysis', 'Analysis not available')}")
                        report_lines.append(f"   Code Context:")
                        # Add indentation to code block
                        code_lines = issue['code'].split('\n')
                        for code_line in code_lines:
                            report_lines.append(f"     {code_line}")
                        if issue['recommendation']:
                            report_lines.append(f"   Fix: {issue['recommendation']}")
        else:
            report_lines.append("No memory leak patterns detected!")
        
        # Memory Optimization Recommendations
        report_lines.append("\n" + "=" * 80)
        report_lines.append("GENERAL MEMORY OPTIMIZATION RECOMMENDATIONS")
        report_lines.append("=" * 80)
        
        recommendations = [
            "1. Always use try-with-resources for closeable objects (Java)",
            "2. Use context managers ('with' statement) for file operations (Python)",
            "3. Implement proper cleanup in finally blocks",
            "4. Set maximum sizes for caches and collections",
            "5. Use weak references for parent-child relationships",
            "6. Avoid static collections that grow indefinitely",
            "7. Clean up event listeners and timers",
            "8. Join threads or use thread pools properly",
            "9. Be cautious with circular references",
            "10. Monitor memory usage in production",
            "11. Use memory profilers to identify actual leaks",
            "12. Implement proper resource lifecycle management"
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
        parser = argparse.ArgumentParser(description='Memory Leak Detector')
        parser.add_argument('directory', help='Directory to scan')
        parser.add_argument('--extensions', nargs='+', 
                           default=['.java', '.py', '.js', '.ts', '.cpp', '.c', '.jar', '.class'],
                           help='File extensions to scan')
        parser.add_argument('--output', help='Output report file')
        parser.add_argument('--json-output', help='JSON output file')
        parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
        
        args = parser.parse_args()
        
        if args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        
        print("Memory Leak Detector Starting...")
        print(f"Scanning directory: {args.directory}")
        print(f"File extensions: {args.extensions}")
        print("-" * 50)
        
        detector = MemoryLeakDetector()
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
        
        print("\n" + "="*50)
        print("Analysis completed successfully!")
        
    except Exception as e:
        print(f"\nERROR: {e}")
        print(f"Stack trace: {traceback.format_exc()}")
        logger.error(f"Main execution error: {e}")
    
    finally:
        # Prevent window from closing immediately
        try:
            input("\nPress Enter to exit...")
        except (EOFError, KeyboardInterrupt):
            pass

if __name__ == '__main__':
    if len(sys.argv) > 1:
        main()
    else:
        # Interactive mode
        try:
            print("Memory Leak Detector")
            print("==================")
            print("Supported file types: .java, .py, .js, .ts, .cpp, .c, .jar, .class")
            print()
            
            directory = input("Enter directory to scan (or press Enter for current directory): ").strip()
            if not directory:
                directory = "."
            
            if not os.path.exists(directory):
                print(f"Directory '{directory}' does not exist!")
                input("Press Enter to exit...")
                sys.exit(1)
            
            print(f"\nScanning directory: {os.path.abspath(directory)}")
            print("This may take a moment for large directories...")
            print("-" * 50)
            
            detector = MemoryLeakDetector()
            results = detector.scan_directory(directory)
            report = detector.generate_report(results)
            print(report)
            
            print("\n" + "="*50)
            print("Analysis completed successfully!")
            
        except Exception as e:
            print(f"\nERROR: {e}")
            print(f"Stack trace: {traceback.format_exc()}")
            
        finally:
            # Prevent window from closing immediately
            try:
                input("\nPress Enter to exit...")
            except (EOFError, KeyboardInterrupt):
                pass
