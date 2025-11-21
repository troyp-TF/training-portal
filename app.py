from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os
from functools import wraps

app = Flask(__name__)

# --- CONFIG ---
app.config["SECRET_KEY"] = "change-this-to-a-random-secret"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///training.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# --- MODELS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="trainee")  # 'admin' or 'trainee'

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class TrainingModule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    lessons = db.relationship(
        "Lesson",
        backref="module",
        lazy=True,
        order_by="Lesson.sort_order"
    )


class Lesson(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    module_id = db.Column(db.Integer, db.ForeignKey("training_module.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text)
    sort_order = db.Column(db.Integer, default=0)


class LessonProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lesson.id"), nullable=False)
    completed_at = db.Column(db.DateTime, default=datetime.utcnow)


# --- HELPERS ---
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


# Make current_user available in ALL templates (this was missing before)
@app.context_processor
def inject_current_user():
    return {"current_user": current_user}


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if not user or user.role != "admin":
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated


# --- ROUTES ---

@app.route("/")
def index():
    if current_user():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            session["user_id"] = user.id
            session["role"] = user.role
            flash("Logged in successfully.", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid email or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


@app.route("/init-admin", methods=["GET", "POST"])
def init_admin():
    # Only allow if there is no admin yet
    existing_admin = User.query.filter_by(role="admin").first()
    if existing_admin:
        flash("Admin already exists. Please log in.", "info")
        return redirect(url_for("login"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("All fields are required.", "danger")
        else:
            if User.query.filter_by(email=email).first():
                flash("Email already in use.", "danger")
            else:
                user = User(
                    name=name,
                    email=email,
                    password_hash=generate_password_hash(password),
                    role="admin",
                )
                db.session.add(user)
                db.session.commit()
                flash("Admin user created. Please log in.", "success")
                return redirect(url_for("login"))

    return render_template("init_admin.html")


# --- TRAINEE DASHBOARD ---

@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    modules = TrainingModule.query.filter_by(is_active=True).all()
    # For each module, count lessons and completed lessons
    progress_map = {}
    for m in modules:
        lesson_ids = [l.id for l in m.lessons]
        total = len(lesson_ids)
        if total == 0:
            completed = 0
        else:
            completed = (
                LessonProgress.query.filter(
                    LessonProgress.user_id == user.id,
                    LessonProgress.lesson_id.in_(lesson_ids),
                )
                .distinct(LessonProgress.lesson_id)
                .count()
            )
        progress_map[m.id] = {"total": total, "completed": completed}

    return render_template("trainee_dashboard.html", user=user, modules=modules, progress_map=progress_map)


@app.route("/modules/<int:module_id>")
@login_required
def module_detail(module_id):
    user = current_user()
    module = TrainingModule.query.get_or_404(module_id)
    lesson_progress = {
        lp.lesson_id: lp
        for lp in LessonProgress.query.filter_by(user_id=user.id).all()
    }
    return render_template(
        "module_detail.html",
        module=module,
        lesson_progress=lesson_progress,
        user=user,
    )


@app.route("/lessons/<int:lesson_id>/complete", methods=["POST"])
@login_required
def complete_lesson(lesson_id):
    user = current_user()
    lesson = Lesson.query.get_or_404(lesson_id)

    existing = LessonProgress.query.filter_by(user_id=user.id, lesson_id=lesson.id).first()
    if not existing:
        lp = LessonProgress(user_id=user.id, lesson_id=lesson.id)
        db.session.add(lp)
        db.session.commit()
        flash("Lesson marked as completed.", "success")
    else:
        flash("Lesson already completed.", "info")

    return redirect(url_for("module_detail", module_id=lesson.module_id))


# --- ADMIN ROUTES ---

@app.route("/admin")
@admin_required
def admin_dashboard():
    modules = TrainingModule.query.order_by(TrainingModule.title).all()
    users = User.query.order_by(User.name).all()
    return render_template("admin_dashboard.html", modules=modules, users=users)


@app.route("/admin/module/new", methods=["GET", "POST"])
@admin_required
def new_module():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        if not title:
            flash("Title is required.", "danger")
        else:
            m = TrainingModule(title=title, description=description, is_active=True)
            db.session.add(m)
            db.session.commit()
            flash("Module created.", "success")
            return redirect(url_for("admin_dashboard"))
    return render_template("module_form.html", module=None)


@app.route("/admin/module/<int:module_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_module(module_id):
    module = TrainingModule.query.get_or_404(module_id)
    if request.method == "POST":
        module.title = request.form.get("title", "").strip()
        module.description = request.form.get("description", "").strip()
        module.is_active = bool(request.form.get("is_active"))
        db.session.commit()
        flash("Module updated.", "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("module_form.html", module=module)


@app.route("/admin/module/<int:module_id>/delete", methods=["POST"])
@admin_required
def delete_module(module_id):
    module = TrainingModule.query.get_or_404(module_id)
    db.session.delete(module)
    db.session.commit()
    flash("Module deleted.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/module/<int:module_id>/lessons/new", methods=["GET", "POST"])
@admin_required
def new_lesson(module_id):
    module = TrainingModule.query.get_or_404(module_id)
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        sort_order = int(request.form.get("sort_order") or 0)
        if not title:
            flash("Title is required.", "danger")
        else:
            l = Lesson(module_id=module.id, title=title, content=content, sort_order=sort_order)
            db.session.add(l)
            db.session.commit()
            flash("Lesson created.", "success")
            return redirect(url_for("edit_module", module_id=module.id))
    return render_template("lesson_form.html", module=module, lesson=None)


@app.route("/admin/lesson/<int:lesson_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_lesson(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    if request.method == "POST":
        lesson.title = request.form.get("title", "").strip()
        lesson.content = request.form.get("content", "").strip()
        lesson.sort_order = int(request.form.get("sort_order") or 0)
        db.session.commit()
        flash("Lesson updated.", "success")
        return redirect(url_for("edit_module", module_id=lesson.module_id))
    return render_template("lesson_form.html", module=lesson.module, lesson=lesson)


@app.route("/admin/lesson/<int:lesson_id>/delete", methods=["POST"])
@admin_required
def delete_lesson(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    module_id = lesson.module_id
    db.session.delete(lesson)
    db.session.commit()
    flash("Lesson deleted.", "info")
    return redirect(url_for("edit_module", module_id=module_id))


# --- CLI: INIT DB ---
@app.cli.command("init-db")
def init_db_command():
    """Initialize the database."""
    db.create_all()
    print("Database initialized.")


if __name__ == "__main__":
    # Create DB file if not exists
    if not os.path.exists("training.db"):
        with app.app_context():
            db.create_all()
            print("Database created.")
    app.run(debug=True)

