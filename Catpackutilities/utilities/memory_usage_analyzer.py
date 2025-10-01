#!/usr/bin/env python3
"""
Memory Usage Analyzer for Minecraft Mods
=========================================

Analyzes JAR files and estimates potential memory usage of Minecraft mods
based on various factors including file sizes, class complexity, textures,
and resource loading patterns.

Author: Cat Pack Utilities
Version: 1.0
Date: 2025-10-01
"""

import os
import sys
import zipfile
import re
import logging
import json
from datetime import datetime
from collections import defaultdict
from enum import Enum
from typing import Dict, List, Tuple, Optional
import argparse

# Configure logging
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from logging_config import configure_logging
configure_logging()
logger = logging.getLogger(__name__)

class MemoryUsageLevel(Enum):
    """Memory usage severity levels"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class MemoryCategory(Enum):
    """Categories of memory usage"""
    CLASSES = "Classes & Code"
    TEXTURES = "Textures & Images"
    SOUNDS = "Audio Files"
    MODELS = "3D Models"
    CONFIGS = "Configuration Files"
    RESOURCES = "Other Resources"
    BYTECODE = "Bytecode Overhead"

class MemoryUsageAnalyzer:
    """Analyzes JAR files for potential memory usage"""
    
    def __init__(self):
        self.total_files_analyzed = 0
        self.results = []
        self.memory_patterns = {
            # Image file patterns and estimated memory multipliers
            'textures': {
                r'\.png$': 4.0,     # PNG files (RGBA = 4 bytes per pixel estimated)
                r'\.jpg$': 3.0,     # JPEG files
                r'\.jpeg$': 3.0,    # JPEG files
                r'\.gif$': 4.0,     # GIF files
                r'\.bmp$': 4.0,     # Bitmap files
                r'textures/': 1.5,  # Files in texture directories
                r'assets.*textures': 1.5,  # Minecraft asset textures
            },
            # Sound file patterns
            'sounds': {
                r'\.ogg$': 1.0,     # OGG sound files
                r'\.wav$': 1.2,     # WAV sound files
                r'\.mp3$': 0.8,     # MP3 sound files
                r'sounds/': 1.0,    # Files in sound directories
                r'assets.*sounds': 1.0,  # Minecraft asset sounds
            },
            # Model file patterns
            'models': {
                r'\.json$': 0.5,    # JSON model files
                r'\.obj$': 2.0,     # OBJ model files
                r'models/': 1.0,    # Files in model directories
                r'assets.*models': 1.0,  # Minecraft asset models
            },
            # Class file patterns
            'classes': {
                r'\.class$': 1.0,   # Java class files
            },
            # Configuration patterns
            'configs': {
                r'\.cfg$': 0.1,     # Configuration files
                r'\.properties$': 0.1,  # Properties files
                r'\.yml$': 0.1,     # YAML files
                r'\.yaml$': 0.1,    # YAML files
                r'\.toml$': 0.1,    # TOML files
                r'\.json$': 0.2,    # JSON config files
            }
        }
        
        # Memory usage thresholds (in MB)
        self.thresholds = {
            MemoryUsageLevel.LOW: 10,
            MemoryUsageLevel.MEDIUM: 50,
            MemoryUsageLevel.HIGH: 150,
            MemoryUsageLevel.CRITICAL: 300
        }

    def analyze_directory(self, directory_path: str) -> Dict:
        """Analyze all JAR files in a directory"""
        logger.info(f"Starting memory usage analysis of directory: {directory_path}")
        
        if not os.path.exists(directory_path):
            logger.error(f"Directory not found: {directory_path}")
            return {"error": f"Directory not found: {directory_path}"}
        
        jar_files = []
        
        # Find all JAR files
        for root, dirs, files in os.walk(directory_path):
            for file in files:
                if file.lower().endswith('.jar'):
                    jar_files.append(os.path.join(root, file))
        
        if not jar_files:
            logger.warning("No JAR files found in the specified directory")
            return {"warning": "No JAR files found"}
        
        logger.info(f"Found {len(jar_files)} JAR files to analyze")
        
        results = {
            "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "directory": directory_path,
            "total_jars": len(jar_files),
            "mods_analyzed": [],
            "summary": {
                "total_estimated_memory": 0,
                "average_memory_per_mod": 0,
                "highest_memory_mod": None,
                "memory_distribution": {level.value: 0 for level in MemoryUsageLevel}
            }
        }
        
        total_memory = 0
        
        for jar_path in jar_files:
            try:
                mod_analysis = self.analyze_jar_file(jar_path)
                if mod_analysis:
                    results["mods_analyzed"].append(mod_analysis)
                    total_memory += mod_analysis["estimated_memory_mb"]
                    results["summary"]["memory_distribution"][mod_analysis["memory_level"]] += 1
                
            except Exception as e:
                logger.error(f"Error analyzing {jar_path}: {str(e)}")
                continue
        
        # Calculate summary statistics
        if results["mods_analyzed"]:
            results["summary"]["total_estimated_memory"] = round(total_memory, 2)
            results["summary"]["average_memory_per_mod"] = round(total_memory / len(results["mods_analyzed"]), 2)
            
            # Find highest memory mod
            highest_mod = max(results["mods_analyzed"], key=lambda x: x["estimated_memory_mb"])
            results["summary"]["highest_memory_mod"] = {
                "name": highest_mod["mod_name"],
                "memory_mb": highest_mod["estimated_memory_mb"]
            }
        
        self.total_files_analyzed = len(results["mods_analyzed"])
        logger.info(f"Analysis complete. Analyzed {self.total_files_analyzed} mods")
        
        return results

    def analyze_jar_file(self, jar_path: str) -> Optional[Dict]:
        """Analyze a single JAR file for memory usage"""
        try:
            with zipfile.ZipFile(jar_path, 'r') as jar:
                file_list = jar.namelist()
                
                mod_analysis = {
                    "mod_name": os.path.basename(jar_path),
                    "file_path": jar_path,
                    "file_size_mb": round(os.path.getsize(jar_path) / (1024 * 1024), 2),
                    "total_files": len(file_list),
                    "memory_breakdown": {category.value: 0 for category in MemoryCategory},
                    "estimated_memory_mb": 0,
                    "memory_level": MemoryUsageLevel.LOW.value,
                    "analysis_details": {
                        "class_files": 0,
                        "texture_files": 0,
                        "sound_files": 0,
                        "model_files": 0,
                        "config_files": 0,
                        "large_files": [],
                        "potential_issues": []
                    }
                }
                
                # Analyze each file in the JAR
                for file_path in file_list:
                    if file_path.endswith('/'):
                        continue  # Skip directories
                    
                    try:
                        file_info = jar.getinfo(file_path)
                        file_size_kb = file_info.file_size / 1024
                        
                        # Analyze file and estimate memory usage
                        memory_estimate = self.estimate_file_memory(file_path, file_size_kb)
                        category = self.categorize_file(file_path)
                        
                        mod_analysis["memory_breakdown"][category.value] += memory_estimate
                        
                        # Track file types
                        if file_path.endswith('.class'):
                            mod_analysis["analysis_details"]["class_files"] += 1
                        elif self.is_texture_file(file_path):
                            mod_analysis["analysis_details"]["texture_files"] += 1
                        elif self.is_sound_file(file_path):
                            mod_analysis["analysis_details"]["sound_files"] += 1
                        elif self.is_model_file(file_path):
                            mod_analysis["analysis_details"]["model_files"] += 1
                        elif self.is_config_file(file_path):
                            mod_analysis["analysis_details"]["config_files"] += 1
                        
                        # Track large files (>1MB)
                        if file_size_kb > 1024:
                            mod_analysis["analysis_details"]["large_files"].append({
                                "file": file_path,
                                "size_mb": round(file_size_kb / 1024, 2),
                                "estimated_memory_mb": round(memory_estimate, 2)
                            })
                    
                    except Exception as e:
                        logger.debug(f"Error analyzing file {file_path} in {jar_path}: {str(e)}")
                        continue
                
                # Calculate total estimated memory
                total_memory = sum(mod_analysis["memory_breakdown"].values())
                mod_analysis["estimated_memory_mb"] = round(total_memory, 2)
                
                # Add bytecode overhead estimation (classes * 2KB average)
                bytecode_overhead = mod_analysis["analysis_details"]["class_files"] * 0.002  # 2KB per class
                mod_analysis["memory_breakdown"][MemoryCategory.BYTECODE.value] = round(bytecode_overhead, 2)
                mod_analysis["estimated_memory_mb"] += bytecode_overhead
                
                # Determine memory usage level
                mod_analysis["memory_level"] = self.determine_memory_level(mod_analysis["estimated_memory_mb"]).value
                
                # Add potential issues
                self.identify_potential_issues(mod_analysis)
                
                return mod_analysis
                
        except Exception as e:
            logger.error(f"Error analyzing JAR file {jar_path}: {str(e)}")
            return None

    def estimate_file_memory(self, file_path: str, file_size_kb: float) -> float:
        """Estimate memory usage for a specific file"""
        file_path_lower = file_path.lower()
        
        # Base memory estimate (file size)
        base_memory = file_size_kb / 1024  # Convert to MB
        
        # Apply multipliers based on file type
        multiplier = 1.0
        
        # Check texture patterns
        for pattern, mult in self.memory_patterns['textures'].items():
            if re.search(pattern, file_path_lower):
                multiplier = max(multiplier, mult)
        
        # Check sound patterns
        for pattern, mult in self.memory_patterns['sounds'].items():
            if re.search(pattern, file_path_lower):
                multiplier = max(multiplier, mult)
        
        # Check model patterns
        for pattern, mult in self.memory_patterns['models'].items():
            if re.search(pattern, file_path_lower):
                multiplier = max(multiplier, mult)
        
        # Check class patterns
        for pattern, mult in self.memory_patterns['classes'].items():
            if re.search(pattern, file_path_lower):
                multiplier = max(multiplier, mult)
        
        # Check config patterns
        for pattern, mult in self.memory_patterns['configs'].items():
            if re.search(pattern, file_path_lower):
                multiplier = max(multiplier, mult)
        
        # Special handling for texture files (estimate based on dimensions)
        if self.is_texture_file(file_path):
            # Assume standard Minecraft texture sizes and calculate memory
            estimated_memory = self.estimate_texture_memory(file_path, file_size_kb)
            return max(base_memory * multiplier, estimated_memory)
        
        return base_memory * multiplier

    def estimate_texture_memory(self, file_path: str, file_size_kb: float) -> float:
        """Estimate memory usage for texture files"""
        # Common Minecraft texture sizes and their memory usage
        # Assuming RGBA format (4 bytes per pixel)
        
        filename = os.path.basename(file_path).lower()
        
        # Standard texture size estimations
        if 'block' in filename or 'item' in filename:
            # Standard 16x16 textures
            return 0.001  # ~1KB in memory
        elif 'entity' in filename or 'mob' in filename:
            # Entity textures are usually larger (64x64 or 128x128)
            return 0.032  # ~32KB in memory
        elif 'gui' in filename:
            # GUI textures vary widely
            return file_size_kb * 2 / 1024  # Estimate 2x file size
        elif 'environment' in filename or 'sky' in filename:
            # Environment textures can be very large
            return file_size_kb * 4 / 1024  # Estimate 4x file size
        else:
            # Generic texture estimation
            return file_size_kb * 3 / 1024  # Estimate 3x file size

    def categorize_file(self, file_path: str) -> MemoryCategory:
        """Categorize a file for memory analysis"""
        file_path_lower = file_path.lower()
        
        if file_path_lower.endswith('.class'):
            return MemoryCategory.CLASSES
        elif self.is_texture_file(file_path):
            return MemoryCategory.TEXTURES
        elif self.is_sound_file(file_path):
            return MemoryCategory.SOUNDS
        elif self.is_model_file(file_path):
            return MemoryCategory.MODELS
        elif self.is_config_file(file_path):
            return MemoryCategory.CONFIGS
        else:
            return MemoryCategory.RESOURCES

    def is_texture_file(self, file_path: str) -> bool:
        """Check if file is a texture/image file"""
        return any(re.search(pattern, file_path.lower()) for pattern in self.memory_patterns['textures'].keys())

    def is_sound_file(self, file_path: str) -> bool:
        """Check if file is a sound file"""
        return any(re.search(pattern, file_path.lower()) for pattern in self.memory_patterns['sounds'].keys())

    def is_model_file(self, file_path: str) -> bool:
        """Check if file is a model file"""
        return any(re.search(pattern, file_path.lower()) for pattern in self.memory_patterns['models'].keys())

    def is_config_file(self, file_path: str) -> bool:
        """Check if file is a configuration file"""
        return any(re.search(pattern, file_path.lower()) for pattern in self.memory_patterns['configs'].keys())

    def determine_memory_level(self, memory_mb: float) -> MemoryUsageLevel:
        """Determine memory usage severity level"""
        if memory_mb >= self.thresholds[MemoryUsageLevel.CRITICAL]:
            return MemoryUsageLevel.CRITICAL
        elif memory_mb >= self.thresholds[MemoryUsageLevel.HIGH]:
            return MemoryUsageLevel.HIGH
        elif memory_mb >= self.thresholds[MemoryUsageLevel.MEDIUM]:
            return MemoryUsageLevel.MEDIUM
        else:
            return MemoryUsageLevel.LOW

    def identify_potential_issues(self, mod_analysis: Dict):
        """Identify potential memory-related issues"""
        issues = []
        
        # Check for excessive texture memory
        texture_memory = mod_analysis["memory_breakdown"][MemoryCategory.TEXTURES.value]
        if texture_memory > 50:
            issues.append(f"High texture memory usage: {texture_memory:.1f}MB")
        
        # Check for large number of classes
        class_count = mod_analysis["analysis_details"]["class_files"]
        if class_count > 500:
            issues.append(f"Large number of classes: {class_count}")
        
        # Check for very large individual files
        large_files = mod_analysis["analysis_details"]["large_files"]
        if large_files:
            largest_file = max(large_files, key=lambda x: x["size_mb"])
            if largest_file["size_mb"] > 10:
                issues.append(f"Very large file: {largest_file['file']} ({largest_file['size_mb']}MB)")
        
        # Check overall mod size vs estimated memory
        file_size = mod_analysis["file_size_mb"]
        estimated_memory = mod_analysis["estimated_memory_mb"]
        if file_size > 0 and estimated_memory > file_size * 3:
            issues.append(f"High memory multiplier: {estimated_memory/file_size:.1f}x file size")
        
        mod_analysis["analysis_details"]["potential_issues"] = issues

    def generate_report(self, results: Dict, output_file: Optional[str] = None) -> str:
        """Generate a comprehensive memory usage report"""
        if "error" in results or "warning" in results:
            return str(results)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        report = []
        report.append("=== MEMORY USAGE ANALYSIS REPORT ===")
        report.append(f"Analysis date: {results['analysis_date']}")
        report.append("=" * 50)
        report.append("")
        
        # Executive Summary
        report.append("🧠 EXECUTIVE SUMMARY")
        report.append(f"Total mods analyzed: {results['total_jars']}")
        report.append(f"Total estimated memory usage: {results['summary']['total_estimated_memory']:.1f}MB")
        report.append(f"Average memory per mod: {results['summary']['average_memory_per_mod']:.1f}MB")
        
        if results['summary']['highest_memory_mod']:
            report.append(f"Highest memory mod: {results['summary']['highest_memory_mod']['name']} ({results['summary']['highest_memory_mod']['memory_mb']:.1f}MB)")
        
        report.append("")
        
        # Memory Distribution
        report.append("⚡ MEMORY USAGE DISTRIBUTION")
        for level, count in results['summary']['memory_distribution'].items():
            if count > 0:
                report.append(f"{level}: {count} mods")
        report.append("")
        
        # Top Memory Users
        mods_by_memory = sorted(results['mods_analyzed'], key=lambda x: x['estimated_memory_mb'], reverse=True)
        report.append("🎯 TOP MEMORY CONSUMERS")
        for i, mod in enumerate(mods_by_memory[:10], 1):
            report.append(f"{i:2d}. {mod['estimated_memory_mb']:6.1f}MB - {mod['mod_name']}")
        report.append("")
        
        # Detailed Analysis
        report.append("🔍 DETAILED ANALYSIS")
        report.append("=" * 50)
        
        for mod in mods_by_memory:
            report.append("")
            report.append(f"🚨 {mod['memory_level']} MEMORY USAGE")
            report.append("-" * 40)
            report.append("")
            report.append(f"📋 {mod['mod_name']}")
            report.append(f"File size: {mod['file_size_mb']}MB")
            report.append(f"Estimated memory usage: {mod['estimated_memory_mb']:.1f}MB")
            if mod['file_size_mb'] > 0:
                report.append(f"Memory multiplier: {mod['estimated_memory_mb']/mod['file_size_mb']:.1f}x")
            else:
                report.append("Memory multiplier: N/A (empty file)")
            report.append("")
            
            # Memory breakdown
            report.append("Memory breakdown by category:")
            for category, memory in mod['memory_breakdown'].items():
                if memory > 0:
                    report.append(f"  {category}: {memory:.1f}MB")
            report.append("")
            
            # File statistics
            details = mod['analysis_details']
            report.append("File statistics:")
            report.append(f"  Class files: {details['class_files']}")
            report.append(f"  Texture files: {details['texture_files']}")
            report.append(f"  Sound files: {details['sound_files']}")
            report.append(f"  Model files: {details['model_files']}")
            report.append(f"  Config files: {details['config_files']}")
            report.append("")
            
            # Large files
            if details['large_files']:
                report.append("Large files (>1MB):")
                for file_info in details['large_files'][:5]:  # Show top 5
                    report.append(f"  {file_info['size_mb']:.1f}MB - {os.path.basename(file_info['file'])}")
                if len(details['large_files']) > 5:
                    report.append(f"  ... and {len(details['large_files']) - 5} more")
                report.append("")
            
            # Potential issues
            if details['potential_issues']:
                report.append("⚠️  Potential issues:")
                for issue in details['potential_issues']:
                    report.append(f"  • {issue}")
                report.append("")
        
        report_text = "\n".join(report)
        
        # Save to file if requested
        if output_file:
            try:
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(report_text)
                logger.info(f"Report saved to: {output_file}")
            except Exception as e:
                logger.error(f"Error saving report: {str(e)}")
        
        return report_text

def main():
    """Main function to run the memory usage analyzer"""
    try:
        parser = argparse.ArgumentParser(description="Analyze memory usage of Minecraft mods")
        parser.add_argument("directory", nargs="?", help="Directory containing JAR files to analyze")
        parser.add_argument("-o", "--output", help="Output file for the report")
        parser.add_argument("-j", "--json", help="Output results as JSON file")
        parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
        parser.add_argument("--no-pause", action="store_true", help="Don't pause at the end (for automation)")
        
        args = parser.parse_args()
        
        if args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        
        analyzer = MemoryUsageAnalyzer()
        
        if args.directory:
            directory = args.directory
        else:
            # Interactive mode
            print("Memory Usage Analyzer for Minecraft Mods")
            print("=" * 50)
            directory = input("Enter directory path (or press Enter to exit): ").strip()
            
            if not directory:
                print("No directory specified. Exiting.")
                if not args.no_pause:
                    input("\nPress Enter to exit...")
                return
        
        print(f"\n🧠 Analyzing memory usage in: {directory}")
        print("This may take a while for large mod collections...")
        
        # Perform analysis
        results = analyzer.analyze_directory(directory)
        
        if "error" in results or "warning" in results:
            print(f"❌ Error: {results}")
            if not args.no_pause:
                input("\nPress Enter to exit...")
            return
        
        # Generate output filename if not specified
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if not args.output:
            report_folder = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'memory_reports')
            os.makedirs(report_folder, exist_ok=True)
            args.output = os.path.join(report_folder, f'memory_report_{timestamp}.txt')
        
        # Generate and save report
        report = analyzer.generate_report(results, args.output)
        
        # Save JSON if requested
        if args.json:
            try:
                with open(args.json, 'w', encoding='utf-8') as f:
                    json.dump(results, f, indent=2, ensure_ascii=False)
                print(f"📄 JSON data saved to: {args.json}")
            except Exception as e:
                print(f"❌ Error saving JSON: {str(e)}")
        
        # Display summary
        print(f"\n✅ Analysis complete!")
        print(f"📊 Analyzed {results['total_jars']} mods")
        print(f"💾 Total estimated memory: {results['summary']['total_estimated_memory']:.1f}MB")
        print(f"📄 Report saved to: {args.output}")
        
        # Show top memory consumers
        if results['mods_analyzed']:
            print(f"\n🎯 Top 5 Memory Consumers:")
            mods_by_memory = sorted(results['mods_analyzed'], key=lambda x: x['estimated_memory_mb'], reverse=True)
            for i, mod in enumerate(mods_by_memory[:5], 1):
                print(f"  {i}. {mod['estimated_memory_mb']:6.1f}MB - {mod['mod_name']} ({mod['memory_level']})")
        
        # Pause to prevent command window from closing
        if not args.no_pause:
            print(f"\n" + "="*50)
            input("Press Enter to exit...")
            
    except KeyboardInterrupt:
        print("\n\n⚠️  Operation cancelled by user")
        input("Press Enter to exit...")
    except Exception as e:
        print(f"\n❌ Unexpected error occurred: {str(e)}")
        print("\nIf this error persists, please report it with the error details above.")
        input("Press Enter to exit...")

if __name__ == "__main__":
    main()