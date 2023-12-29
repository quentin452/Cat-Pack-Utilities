package fr.iamacat.catpackutilities;

import com.badlogic.gdx.backends.lwjgl3.Lwjgl3Application;
import com.badlogic.gdx.backends.lwjgl3.Lwjgl3ApplicationConfiguration;
import main.fr.iamacat.catpackutilities.CatPackUtilities;

// Please note that on macOS your application needs to be started with the -XstartOnFirstThread JVM argument
public class DesktopLauncher {
	public static int initialScreenWidth = 1280;
	public static int initialScreenHeight = 720;
	public static void main(String[] arg) {
		Lwjgl3ApplicationConfiguration config = new Lwjgl3ApplicationConfiguration();
		config.setForegroundFPS(60);
		config.setTitle("Cat-Pack-Utilities");
		config.setWindowIcon("cat.png");

		config.setWindowedMode(initialScreenWidth, initialScreenHeight);

		new Lwjgl3Application(new CatPackUtilities(), config);
	}
}