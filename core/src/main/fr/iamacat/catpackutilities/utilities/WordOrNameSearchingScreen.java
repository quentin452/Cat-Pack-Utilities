package main.fr.iamacat.catpackutilities.utilities;

import com.badlogic.gdx.Gdx;
import com.badlogic.gdx.Screen;
import com.badlogic.gdx.graphics.GL20;
import com.badlogic.gdx.graphics.Texture;
import com.badlogic.gdx.graphics.g2d.SpriteBatch;
import com.badlogic.gdx.scenes.scene2d.InputEvent;
import com.badlogic.gdx.scenes.scene2d.Stage;
import com.badlogic.gdx.scenes.scene2d.ui.Skin;
import com.badlogic.gdx.scenes.scene2d.ui.TextButton;
import com.badlogic.gdx.scenes.scene2d.utils.ClickListener;
import main.fr.iamacat.catpackutilities.CatPackUtilities;

public class WordOrNameSearchingScreen implements Screen {

    private final Stage stage;
    private SpriteBatch batch;
    private Texture backgroundTexture;
    private float backgroundWidth;
    private float backgroundHeight;
    private Skin skin;
    private TextButton searchButton;
    private TextButton backButton;
    private final CatPackUtilities mainApp;
    public WordOrNameSearchingScreen(CatPackUtilities mainApp) {
        this.mainApp = mainApp;
        stage = new Stage();
    }

    @Override
    public void show() {
        batch = new SpriteBatch();

        stage.clear();

        skin = new Skin(Gdx.files.internal("skin/craftacular/skin/craftacular-ui.json"));
        backgroundTexture = new Texture(Gdx.files.internal("new_background.png"));

        createButtons();

        backgroundWidth = Gdx.graphics.getWidth();
        backgroundHeight = Gdx.graphics.getHeight();
        Gdx.input.setInputProcessor(stage);
    }

    private void createButtons() {
        searchButton = createButton("Search");
        searchButton.setPosition((Gdx.graphics.getWidth() - searchButton.getWidth()) / 2f,
                (Gdx.graphics.getHeight() - searchButton.getHeight()) / 2f);

        searchButton.addListener(new ClickListener() {
            @Override
            public void clicked(InputEvent event, float x, float y) {
                try {
                    String os = System.getProperty("os.name").toLowerCase();
                    ProcessBuilder builder = new ProcessBuilder();

                    if (os.contains("win")) { // Pour Windows
                        String classPath = "<path_to_your_classes>"; // Remplacez par le chemin absolu de votre classe WordOrNameSearching
                        builder.command("cmd.exe", "/c", "start", "java", "-classpath", classPath, "main.fr.iamacat.catpackutilities.utilities.WordOrNameSearching");
                    } else if (os.contains("nix") || os.contains("nux") || os.contains("mac")) { // Pour Linux ou MacOS
                        String classPath = "<path_to_your_classes>"; // Remplacez par le chemin absolu de votre classe WordOrNameSearching
                        builder.command("x-terminal-emulator", "-e", "java", "-classpath", classPath, "main.fr.iamacat.catpackutilities.utilities.WordOrNameSearching");
                    } else {
                        System.out.println("Système d'exploitation non pris en charge pour ouvrir le terminal.");
                        return;
                    }

                    builder.start();
                } catch (Exception e) {
                    e.printStackTrace();
                }
            }
        });

        backButton = createButton("Back to Menu");
        backButton.setPosition((Gdx.graphics.getWidth() - searchButton.getWidth()) / 1.5f,
                (Gdx.graphics.getHeight() - searchButton.getHeight()) / 1.5f);

        backButton.addListener(new ClickListener() {
            @Override
            public void clicked(InputEvent event, float x, float y) {
                if (mainApp != null) {
                    mainApp.showMainMenu();
                    Gdx.app.log("WordOrNameSearchingScreen", "Back to Menu button clicked");
                }
            }
        });

        stage.addActor(backButton);
        stage.addActor(searchButton);
    }

    private TextButton createButton(String text) {
        TextButton button = new TextButton(text, skin);
        button.setSize(200, 50);
        return button;
    }

    @Override
    public void render(float delta) {
        Gdx.gl.glClearColor(0, 0, 0, 1);
        Gdx.gl.glClear(GL20.GL_COLOR_BUFFER_BIT);

        if (batch != null) {
            batch.begin();
            batch.draw(backgroundTexture, 0, 0, backgroundWidth, backgroundHeight);
            batch.end();
        }

        stage.act(delta);
        stage.draw();
    }


    @Override
    public void resize(int width, int height) {
        stage.getViewport().update(width, height);

        float searchButtonX = (stage.getWidth() - searchButton.getWidth()) / 2f;
        float searchButtonY = (stage.getHeight() - searchButton.getHeight()) / 2f;
        searchButton.setPosition(searchButtonX, searchButtonY);

        float backButtonX = (stage.getWidth() - backButton.getWidth()) / 2f;
        float backButtonY = (stage.getHeight() - backButton.getHeight()) / 1.5f;
        backButton.setPosition(backButtonX, backButtonY);

        backgroundWidth = width;
        backgroundHeight = height;
    }

    @Override
    public void pause() {}
    @Override
    public void resume() {}
    @Override
    public void hide() {
        Gdx.input.setInputProcessor(null);
    }
    @Override
    public void dispose() {
        batch.dispose();
        backgroundTexture.dispose();
        stage.dispose();
        skin.dispose();
        if (backButton != null) {
            backButton.clearListeners();
            backButton.remove();
            backButton = null;
        }
    }
    public Stage getStage() {
        return stage;
    }
}